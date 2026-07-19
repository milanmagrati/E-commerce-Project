"""
trendycrm/ai_router.py
======================
Multi-Model AI Routing Engine for Trendy CRM.

Architecture:
  1. Intent Classifier  — uses Gemini Flash (cheapest, fastest) to bucket the input.
  2. Model Router       — sends the request to the right AI model based on:
                          • Input type  (text / image / audio)
                          • Classified intent (purchase / query / issue / spam)
  3. Dynamic Prompt     — injects CRMPageProfile data into every AI call so the
                          AI always knows the product, price, tone, and checkout link.

Models used:
  • Gemini Flash (google-generativeai)  — Simple text / FAQ / intent classification
  • GPT-4o (openai)                     — Complex text / sales logic / image OCR
  • Gemini (native audio)               — Audio / voice streams (future use)
"""

import logging
import os
import json

logger = logging.getLogger(__name__)

# ─── Lazy imports (only load SDKs when actually needed) ───────────────────────
def _get_openai_client(api_key=None):
    try:
        from openai import OpenAI
        if not api_key or api_key.startswith('sk-your-'):
            raise ValueError("OpenAI API key not configured.")
        return OpenAI(api_key=api_key)
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")


def _get_gemini_model(model_name: str = 'gemini-1.5-flash', api_key: str = None):
    try:
        import google.generativeai as genai
        if not api_key or api_key == 'your-gemini-api-key-here':
            raise ValueError("Gemini API key not configured.")
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model_name)
    except ImportError:
        raise ImportError("google-generativeai package not installed. Run: pip install google-generativeai")


from decouple import config as env_config

# ─── Fetching config from Env ──────────────────────────────────────────────────
def _get_ai_config():
    """
    Fetch the API keys from environment variables using decouple to parse the .env file.
    """
    return {
        'openai_api_key': env_config('OPENAI_API_KEY', default=''),
        'gemini_api_key': env_config('GEMINI_API_KEY', default=''),
    }


def _get_page_profile(integration):
    """
    Fetch the CRMPageProfile for a given integration.
    Returns None if no profile exists for this page.
    """
    try:
        return integration.page_profile
    except Exception:
        return None


# ─── Dynamic Prompt Assembly ──────────────────────────────────────────────────
def build_dynamic_prompt(chatbot_config, page_profile=None, intent: str = 'general_query') -> str:
    """
    Assembles the AI system prompt by injecting:
      • Global business knowledge (from CRMChatbotConfig)
      • Page-specific data (product, price, tone, checkout link from CRMPageProfile)
      • Behavioral instructions based on classified intent

    This replaces the need for 50+ separate AI prompts.
    """
    biz_name = chatbot_config.business_name or 'our business'
    about = chatbot_config.about_blurb or ''
    welcome = chatbot_config.welcome_message or ''

    # Base system context
    system_parts = [
        f"You are a helpful AI sales and support assistant for {biz_name}.",
        f"About the business: {about}" if about else "",
        f"Welcome message to use: {welcome}" if welcome else "",
    ]

    if getattr(chatbot_config, 'primary_language', 'auto') != 'auto':
        lang_map = {
            'en': 'English', 'es': 'Spanish', 'fr': 'French', 
            'ne': 'Nepali', 'hi': 'Hindi'
        }
        lang_name = lang_map.get(chatbot_config.primary_language, 'English')
        system_parts.append(f"\nCRITICAL RULE: You MUST reply entirely in {lang_name}.")

    # Page-specific injection (The "Centralized Knowledge Core" in action)
    if page_profile:
        tone_map = {
            'friendly': 'Be warm, casual, and conversational. Use emojis occasionally.',
            'professional': 'Be formal, precise, and polite. Use professional language.',
            'energetic': 'Be enthusiastic and energetic! Use exclamation marks and hype language.',
            'empathetic': 'Be empathetic, patient, and supportive. Acknowledge feelings first.',
            'luxury': 'Be elegant and exclusive. Use premium, aspirational language.',
        }
        tone_instruction = tone_map.get(page_profile.brand_tone, tone_map['friendly'])

        system_parts += [
            f"\n--- PAGE-SPECIFIC PRODUCT PROFILE ---",
            f"Product: {page_profile.product_name}" if page_profile.product_name else "",
            f"Price: {page_profile.price}" if page_profile.price else "",
            f"Checkout/Order Link: {page_profile.checkout_link}" if page_profile.checkout_link else "",
            f"Custom FAQ / Product Info: {page_profile.custom_faq}" if page_profile.custom_faq else "",
            f"\nTone Instruction: {tone_instruction}",
        ]

    # Intent-based behavioral rules
    intent_rules = {
        'purchase_intent': (
            "\n--- SALES MODE ACTIVE ---\n"
            "The customer is interested in buying. Your primary goal is to close the sale.\n"
            "1. Acknowledge their interest enthusiastically.\n"
            "2. Highlight the key benefit of the product in 1-2 sentences.\n"
            "3. Include the checkout link naturally at the end of your message.\n"
            "4. Keep the message short and action-oriented. Do NOT write long paragraphs.\n"
            "IMPORTANT: Always include the checkout link in your DM reply."
        ),
        'general_query': (
            "\n--- FAQ MODE ACTIVE ---\n"
            "Answer the customer's question accurately and concisely using the product info above.\n"
            "Be helpful, clear, and friendly. Keep answers under 3 sentences where possible.\n"
            "Do NOT push for a sale unless they ask about buying."
        ),
        'product_issue': (
            "\n--- SUPPORT MODE ACTIVE ---\n"
            "The customer has a product issue or complaint. NEVER push sales.\n"
            "1. Start by sincerely apologizing for the inconvenience.\n"
            "2. Acknowledge their specific issue with empathy.\n"
            "3. Ask for their order details (order number or contact info) to resolve the issue.\n"
            "A support ticket has been opened automatically. Do NOT mention this to the customer."
        ),
        'spam_noise': (
            "\n--- MINIMAL RESPONSE MODE ---\n"
            "The message appears to be spam or just noise (emojis, random characters).\n"
            "Respond with a brief, friendly acknowledgment only. Do not engage deeply."
        ),
    }

    system_parts.append(intent_rules.get(intent, intent_rules['general_query']))
    return "\n".join(p for p in system_parts if p)


# ─── Step 1: Intent Classifier ────────────────────────────────────────────────
def classify_intent(message_text: str, config: dict) -> str:
    """
    Classifies a message into one of:
      - 'purchase_intent'
      - 'general_query'
      - 'product_issue'
      - 'spam_noise'

    Uses Gemini Flash if available, otherwise falls back to OpenAI GPT-4o-mini.
    Returns 'general_query' on any error.
    """
    if not message_text or not message_text.strip():
        return 'spam_noise'

    classification_prompt = f"""You are a message intent classifier for an e-commerce business.
Classify the following customer message into EXACTLY ONE of these categories:
- purchase_intent: The customer wants to buy, asks about price, payment, or how to order.
- general_query: The customer asks about product details, delivery, ingredients, availability, etc.
- product_issue: The customer has a complaint, wants a refund, or reports a damaged/wrong product.
- spam_noise: The message is just emojis, random text, greetings only, or incomprehensible noise.

Customer message: "{message_text}"

Respond with ONLY the category name, nothing else. No explanation, no punctuation."""

    gemini_key = config.get('gemini_api_key', '')
    openai_key = config.get('openai_api_key', '')

    try:
        response_text = ""
        if gemini_key:
            model = _get_gemini_model('gemini-1.5-flash', api_key=gemini_key)
            response = model.generate_content(classification_prompt)
            response_text = response.text
        elif openai_key:
            client = _get_openai_client(api_key=openai_key)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[{'role': 'user', 'content': classification_prompt}],
                max_tokens=10,
                temperature=0.1
            )
            response_text = response.choices[0].message.content
        else:
            logger.warning("No API keys configured for intent classification.")
            return 'general_query'

        intent = response_text.strip().lower().replace(' ', '_')
        valid_intents = {'purchase_intent', 'general_query', 'product_issue', 'spam_noise'}
        
        if intent in valid_intents:
            return intent
        # Try partial match
        for v in valid_intents:
            if v in intent:
                return v
                
        logger.warning(f"Intent classifier returned unexpected value: '{intent}'. Defaulting to general_query.")
        return 'general_query'
        
    except Exception as e:
        logger.error(f"Intent classification failed: {e}. Defaulting to general_query.")
        return 'general_query'


# ─── Step 2: Model Router ─────────────────────────────────────────────────────
def route_message(
    message_text: str,
    integration=None,
    chatbot_config=None,
    input_type: str = 'text',   # 'text' | 'image' | 'audio'
    force_intent: str = None,   # Skip classification and force a specific intent
) -> dict:
    """
    Master routing function. Accepts a customer message and returns:
      {
        'reply': str,           # The AI-generated reply text
        'intent': str,          # Classified intent bucket
        'model_used': str,      # Which AI model was invoked
        'checkout_link': str,   # Checkout link from page profile (if any)
        'open_ticket': bool,    # True if a support ticket should be opened
        'success': bool,
        'error': str | None,
      }
    """
    result = {
        'reply': '',
        'intent': 'general_query',
        'model_used': '',
        'checkout_link': '',
        'open_ticket': False,
        'success': False,
        'error': None,
    }

    try:
        config = _get_ai_config()
        openai_key = config['openai_api_key']
        gemini_key = config['gemini_api_key']

        if not openai_key and not gemini_key:
            return {'success': False, 'error': 'No AI API keys configured. Please add GEMINI_API_KEY to your .env file.', 'reply': ''}

        if chatbot_config is None:
            return {'success': False, 'error': 'Chatbot configuration is missing or inactive'}

        page_profile = _get_page_profile(integration) if integration else None
        if page_profile:
            result['checkout_link'] = page_profile.checkout_link or ''

        if input_type == 'image':
            # Images → GPT-4o Vision (OCR, object recognition)
            result['intent'] = force_intent or 'general_query'
            if openai_key:
                result['model_used'] = 'gpt-4o-vision'
                system_prompt = build_dynamic_prompt(chatbot_config, page_profile, result['intent'])
                reply = _call_openai_vision(message_text, system_prompt, openai_key, config=chatbot_config)
            elif gemini_key:
                result['model_used'] = 'gemini-vision'
                system_prompt = build_dynamic_prompt(chatbot_config, page_profile, result['intent'])
                reply = _call_gemini_vision(message_text, system_prompt, gemini_key, config=chatbot_config)
            else:
                result['model_used'] = 'error'
                reply = "I'm sorry, I cannot process images without an AI API key configured."
            result['reply'] = reply
            result['success'] = True
            return result

        if input_type == 'audio':
            # Audio → Gemini (native audio processing)
            result['intent'] = force_intent or 'general_query'
            result['model_used'] = 'gemini-audio'
            result['reply'] = "[Audio processing via Gemini — transcription pending integration]"
            result['success'] = True
            return result

        # ── Text Messages → Classify Intent First ─────────────────────────────
        if force_intent:
            intent = force_intent
        else:
            intent = classify_intent(message_text, config)

        result['intent'] = intent

        # Determine which model to use based on intent
        use_premium_model = intent in ('purchase_intent', 'product_issue')

        # Human Handoff / Ticketing logic
        triggers = getattr(chatbot_config, 'handoff_triggers', [])
        result['open_ticket'] = False
        if triggers and isinstance(triggers, list):
            lower_msg = message_text.lower()
            for trigger in triggers:
                if trigger.lower() in lower_msg:
                    result['open_ticket'] = True
                    break

        if intent == 'spam_noise':
            system_prompt = build_dynamic_prompt(chatbot_config, page_profile, intent)
            if gemini_key:
                result['model_used'] = 'gemini-flash'
                result['reply'] = _call_gemini_flash(message_text, system_prompt, gemini_key, chatbot_config)
            elif openai_key:
                result['model_used'] = 'gpt-4o-mini'
                result['reply'] = _call_openai_chat(message_text, system_prompt, openai_key, model='gpt-4o-mini', config=chatbot_config)
            else:
                result['reply'] = "Thanks for your message!"
            result['success'] = True
            return result

        system_prompt = build_dynamic_prompt(chatbot_config, page_profile, intent)

        if use_premium_model:
            if openai_key:
                # Complex / Sales → GPT-4o Premium
                result['model_used'] = 'gpt-4o'
                result['reply'] = _call_openai_chat(message_text, system_prompt, openai_key, config=chatbot_config)
            elif gemini_key:
                # Complex / Sales (OpenAI missing) → Gemini 1.5 Pro
                result['model_used'] = 'gemini-pro'
                result['reply'] = _call_gemini_pro(message_text, system_prompt, gemini_key, chatbot_config)
        else:
            if gemini_key:
                # Simple FAQ → Gemini Flash
                result['model_used'] = 'gemini-flash'
                result['reply'] = _call_gemini_flash(message_text, system_prompt, gemini_key, chatbot_config)
            elif openai_key:
                # Simple FAQ → GPT-4o-mini
                result['model_used'] = 'gpt-4o-mini'
                result['reply'] = _call_openai_chat(message_text, system_prompt, openai_key, model='gpt-4o-mini', config=chatbot_config)

        result['success'] = True

    except Exception as e:
        logger.exception(f"AI Router failed: {e}")
        result['error'] = str(e)
        result['reply'] = "I'm sorry, I'm having trouble processing your request right now. Please try again shortly."

    return result


# ─── Model Callers ────────────────────────────────────────────────────────────
def _call_gemini_flash(user_message: str, system_prompt: str, api_key: str, config=None) -> str:
    """Calls Gemini 1.5 Flash — low cost, high speed. For FAQs and spam."""
    import google.generativeai as genai
    model = _get_gemini_model('gemini-1.5-flash', api_key=api_key)
    full_prompt = f"{system_prompt}\n\nCustomer message: {user_message}"
    
    gen_config = genai.types.GenerationConfig()
    if config:
        gen_config.temperature = float(getattr(config, 'creativity_level', '0.7'))
        gen_config.max_output_tokens = int(getattr(config, 'response_length', '500'))
        
    response = model.generate_content(full_prompt, generation_config=gen_config)
    return response.text.strip()


def _call_openai_chat(user_message: str, system_prompt: str, api_key: str, model: str = 'gpt-4o', config=None) -> str:
    """Calls OpenAI Chat API. Defaults to gpt-4o, but can accept gpt-4o-mini for cost-savings."""
    client = _get_openai_client(api_key=api_key)
    
    temp = float(getattr(config, 'creativity_level', '0.7')) if config else 0.7
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message},
        ],
        max_tokens=max_tokens,
        temperature=temp,
    )
    return response.choices[0].message.content.strip()


def _call_openai_vision(image_description: str, system_prompt: str, api_key: str, config=None) -> str:
    """
    Calls GPT-4o Vision for image analysis (OCR, product labels, barcodes).
    In a real implementation, you'd pass the base64-encoded image URL here.
    """
    client = _get_openai_client(api_key=api_key)
    
    temp = float(getattr(config, 'creativity_level', '0.7')) if config else 0.5
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    
    response = client.chat.completions.create(
        model='gpt-4o',
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f"The customer sent an image. Description/context: {image_description}"},
        ],
        max_tokens=max_tokens,
        temperature=temp,
    )
    return response.choices[0].message.content.strip()


def _call_gemini_pro(user_message: str, system_prompt: str, api_key: str, config=None) -> str:
    """Calls Gemini 1.5 Pro — premium model. For complex reasoning and sales when OpenAI is missing."""
    import google.generativeai as genai
    model = _get_gemini_model('gemini-1.5-pro', api_key=api_key)
    full_prompt = f"{system_prompt}\n\nCustomer message: {user_message}"
    
    gen_config = genai.types.GenerationConfig()
    if config:
        gen_config.temperature = float(getattr(config, 'creativity_level', '0.7'))
        gen_config.max_output_tokens = int(getattr(config, 'response_length', '500'))
        
    response = model.generate_content(full_prompt, generation_config=gen_config)
    return response.text.strip()


def _call_gemini_vision(image_description: str, system_prompt: str, api_key: str, config=None) -> str:
    """
    Calls Gemini 1.5 Flash (multimodal) for image analysis.
    In a real implementation, you'd pass the actual image object.
    """
    import google.generativeai as genai
    model = _get_gemini_model('gemini-1.5-flash', api_key=api_key)
    full_prompt = f"{system_prompt}\n\nThe customer sent an image. Description/context: {image_description}"
    
    gen_config = genai.types.GenerationConfig()
    if config:
        gen_config.temperature = float(getattr(config, 'creativity_level', '0.7'))
        gen_config.max_output_tokens = int(getattr(config, 'response_length', '500'))
        
    response = model.generate_content(full_prompt, generation_config=gen_config)
    return response.text.strip()


# ─── Comment-to-DM Funnel ────────────────────────────────────────────────────
def process_comment_to_dm(comment, integration, chatbot_config=None):
    """
    The ManyChat-style Comment-to-DM Sales Funnel.

    Step 1 (Hook): A customer comments on a post.
    Step 2 (Public Reply): Sends a quick public reply to boost engagement.
    Step 3 (Private DM): Sends a personalized sales pitch + checkout link via DM.

    Args:
        comment: CRMSocialComment instance
        integration: CRMIntegration instance
        chatbot_config: CRMChatbotConfig instance (optional)

    Returns:
        dict with keys: public_reply_sent, dm_sent, intent, model_used, error
    """
    from .meta_sync import reply_to_meta_comment, send_meta_message

    result = {
        'public_reply_sent': False,
        'dm_sent': False,
        'intent': 'general_query',
        'model_used': '',
        'error': None,
    }

    try:
        if chatbot_config is None:
            return {'error': 'Chatbot configuration is missing or inactive'}

        page_profile = _get_page_profile(integration)

        # Skip automation if no profile or if disabled on this page
        if not page_profile or not page_profile.comment_auto_reply_enabled:
            logger.info(f"Comment-to-DM skipped: profile missing or disabled for integration {integration.pk}")
            return result

        message_text = comment.message or ''
        sender_id = comment.sender_id

        # ── Intent Classification ─────────────────────────────────────────────
        config = _get_ai_config()
        intent = classify_intent(message_text, config)
        result['intent'] = intent

        # Spam → do nothing
        if intent == 'spam_noise':
            logger.info(f"Comment classified as spam_noise — skipping Comment-to-DM funnel")
            return result

        # ── Step 2: Public Reply (The Hook) ───────────────────────────────────
        public_reply_text = (
            page_profile.public_reply_template
            or "Just sent the link to your DMs! 💌"
        )
        try:
            if reply_to_meta_comment(integration, comment.meta_comment_id, public_reply_text):
                result['public_reply_sent'] = True
                logger.info(f"Public reply sent to comment {comment.meta_comment_id}")
        except Exception as e:
            logger.error(f"Failed to send public reply: {e}")

        # ── Step 3: Private DM (The Pitch) ────────────────────────────────────
        if sender_id:
            dm_result = route_message(
                message_text=message_text,
                integration=integration,
                chatbot_config=chatbot_config,
                input_type='text',
                force_intent=intent,
            )
            result['model_used'] = dm_result.get('model_used', '')

            if dm_result.get('success') and dm_result.get('reply'):
                dm_text = dm_result['reply']
                # Always append the checkout link to DM if this is purchase intent
                checkout = dm_result.get('checkout_link', '')
                if checkout and intent == 'purchase_intent' and checkout not in dm_text:
                    dm_text += f"\n\n👉 Order here: {checkout}"

                if send_meta_message(integration, sender_id, dm_text):
                    result['dm_sent'] = True
                    logger.info(f"DM sent to {sender_id} (intent={intent}, model={result['model_used']})")
                else:
                    logger.warning(f"send_meta_message failed for sender_id={sender_id}")

        # ── Auto-open support ticket for product issues ────────────────────────
        if intent == 'product_issue':
            try:
                from .models import CRMTicket, CRMContact
                contact, _ = CRMContact.objects.get_or_create(
                    meta_id=sender_id,
                    defaults={'name': comment.sender_name or f'User {sender_id}'}
                )
                CRMTicket.objects.create(
                    title=f"Product Issue from {comment.sender_name} on {integration}",
                    description=f"Comment: {message_text}\nComment ID: {comment.meta_comment_id}",
                    contact=contact,
                    priority='medium',
                    status='open',
                )
                logger.info(f"Support ticket auto-created for sender {comment.sender_name}")
            except Exception as e:
                logger.error(f"Failed to auto-create support ticket: {e}")

    except Exception as e:
        logger.exception(f"process_comment_to_dm failed: {e}")
        result['error'] = str(e)

    return result
