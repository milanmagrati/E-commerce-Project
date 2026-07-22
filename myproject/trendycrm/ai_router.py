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
import requests

logger = logging.getLogger(__name__)

# Google's newer Gemini models are "thinking" models by default and will burn the
# whole max_output_tokens budget on hidden reasoning tokens before ever emitting
# visible text unless thinking is explicitly disabled — see _call_gemini_rest.
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GEMINI_FLASH_MODEL = 'gemini-flash-latest'
GEMINI_PRO_MODEL = 'gemini-pro-latest'


# ─── Lazy imports (only load SDKs when actually needed) ───────────────────────
def _get_openai_client(api_key=None):
    try:
        from openai import OpenAI
        if not api_key or api_key.startswith('sk-your-'):
            raise ValueError("OpenAI API key not configured.")
        return OpenAI(api_key=api_key)
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")


def _call_gemini_rest(model_name: str, system_prompt: str, user_message: str, api_key: str,
                       temperature: float = 0.7, max_tokens: int = 500) -> str:
    """
    Calls the Gemini REST API directly instead of the google-generativeai SDK.

    The SDK depends on grpc, whose compiled extension (cygrpc) gets blocked by
    Windows Application Control Policy on this host — the REST API has no such
    dependency and uses the exact same backend, so this is a drop-in replacement.
    """
    if not api_key or api_key == 'your-gemini-api-key-here':
        raise ValueError("Gemini API key not configured.")

    url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={api_key}"
    payload = {
        'contents': [{'parts': [{'text': user_message}]}],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
            'thinkingConfig': {'thinkingBudget': 0},
        },
    }
    if system_prompt:
        payload['systemInstruction'] = {'parts': [{'text': system_prompt}]}

    response = requests.post(url, json=payload, timeout=30)
    if not response.ok:
        raise ValueError(f"Gemini API error {response.status_code}: {_extract_gemini_error(response)}")
    data = response.json()

    candidates = data.get('candidates') or []
    if not candidates:
        block_reason = data.get('promptFeedback', {}).get('blockReason', 'unknown')
        raise ValueError(f"Gemini returned no candidates (blockReason={block_reason})")

    parts = candidates[0].get('content', {}).get('parts', [])
    text = ''.join(p.get('text', '') for p in parts if 'text' in p).strip()
    if not text:
        raise ValueError(f"Gemini returned empty text (finishReason={candidates[0].get('finishReason')})")
    return text


def _extract_gemini_error(response) -> str:
    """Pulls Google's actual error message/status out of a failed response body."""
    try:
        err = response.json().get('error', {})
        return f"{err.get('status', 'UNKNOWN')} - {err.get('message', response.text)}"
    except Exception:
        return response.text[:500]


def _call_gemini_vision_rest(model_name: str, system_prompt: str, prompt_text: str,
                              image_b64: str, mime_type: str, api_key: str,
                              temperature: float = 0.5, max_tokens: int = 500) -> str:
    """
    Multimodal Gemini REST call: sends a text prompt + an inline image so the model
    can actually read the picture (OCR, product labels, screenshots, barcodes).

    Mirrors _call_gemini_rest's request/error handling but adds an inline_data part
    carrying the base64-encoded image bytes.
    """
    if not api_key or api_key == 'your-gemini-api-key-here':
        raise ValueError("Gemini API key not configured.")

    url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={api_key}"
    payload = {
        'contents': [{
            'parts': [
                {'text': prompt_text},
                {'inline_data': {'mime_type': mime_type or 'image/jpeg', 'data': image_b64}},
            ]
        }],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
            'thinkingConfig': {'thinkingBudget': 0},
        },
    }
    if system_prompt:
        payload['systemInstruction'] = {'parts': [{'text': system_prompt}]}

    response = requests.post(url, json=payload, timeout=45)
    if not response.ok:
        raise ValueError(f"Gemini vision API error {response.status_code}: {_extract_gemini_error(response)}")
    data = response.json()

    candidates = data.get('candidates') or []
    if not candidates:
        block_reason = data.get('promptFeedback', {}).get('blockReason', 'unknown')
        raise ValueError(f"Gemini vision returned no candidates (blockReason={block_reason})")

    parts = candidates[0].get('content', {}).get('parts', [])
    text = ''.join(p.get('text', '') for p in parts if 'text' in p).strip()
    if not text:
        raise ValueError(f"Gemini vision returned empty text (finishReason={candidates[0].get('finishReason')})")
    return text


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


# ─── Provider Availability & Resolution ───────────────────────────────────────
def _get_available_providers(config=None) -> dict:
    """Returns {'openai': bool, 'gemini': bool} based on which keys are configured."""
    if config is None:
        config = _get_ai_config()
    return {
        'openai': bool((config.get('openai_api_key') or '').strip()),
        'gemini': bool((config.get('gemini_api_key') or '').strip()),
    }


def _resolve_provider(preference: str, available: dict, auto_order=None):
    """
    Resolves a purpose's provider preference against what's actually configured.

    Args:
        preference:  'auto' | 'openai' | 'gemini'
        available:   {'openai': bool, 'gemini': bool}
        auto_order:  provider priority list used when preference == 'auto'.

    Returns (provider, fell_back):
        provider  — 'openai' | 'gemini' | None   (None means no key at all)
        fell_back — True when the explicitly-requested provider had no key and we
                    substituted the other one so the feature still works.
    """
    if auto_order is None:
        auto_order = ['gemini', 'openai']  # cheapest first

    if preference in ('openai', 'gemini'):
        if available.get(preference):
            return preference, False
        other = 'gemini' if preference == 'openai' else 'openai'
        if available.get(other):
            return other, True
        return None, False

    # 'auto' (or anything unexpected) → first available in priority order
    for p in auto_order:
        if available.get(p):
            return p, False
    return None, False


def _gen_text(provider: str, model_name: str, user_message: str, system_prompt: str,
              config: dict, chatbot_config=None):
    """
    Generates a text reply from an explicitly-chosen provider using the model name
    configured on the chatbot. Returns (reply_text, model_label).
    """
    if provider == 'openai':
        model = (model_name or 'gpt-4o').strip()
        reply = _call_openai_chat(user_message, system_prompt, config['openai_api_key'],
                                  model=model, config=chatbot_config)
        return reply, model

    # gemini
    model = (model_name or GEMINI_FLASH_MODEL).strip()
    temperature = float(getattr(chatbot_config, 'creativity_level', '0.7')) if chatbot_config else 0.7
    max_tokens = int(getattr(chatbot_config, 'response_length', '500')) if chatbot_config else 500
    if 'pro' in model:
        # Pro models can be quota-0 on free-tier keys — fall back to Flash on failure.
        try:
            reply = _call_gemini_rest(model, system_prompt, user_message, config['gemini_api_key'],
                                      temperature=temperature, max_tokens=max_tokens)
        except Exception as e:
            logger.warning(f"Gemini '{model}' failed ({e}), falling back to {GEMINI_FLASH_MODEL}.")
            reply = _call_gemini_rest(GEMINI_FLASH_MODEL, system_prompt, user_message, config['gemini_api_key'],
                                      temperature=temperature, max_tokens=max_tokens)
            model = GEMINI_FLASH_MODEL
    else:
        reply = _call_gemini_rest(model, system_prompt, user_message, config['gemini_api_key'],
                                  temperature=temperature, max_tokens=max_tokens)
    return reply, model


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
            response_text = _call_gemini_rest(
                GEMINI_FLASH_MODEL, None, classification_prompt, gemini_key,
                temperature=0.1, max_tokens=20,
            )
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
    image_data: str = None,     # base64-encoded image bytes (when input_type='image')
    image_mime: str = None,     # mime type of the image, e.g. 'image/jpeg'
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
        'notice': None,   # set when a requested provider was unavailable and we fell back
    }

    try:
        config = _get_ai_config()
        openai_key = config['openai_api_key']
        gemini_key = config['gemini_api_key']
        available = _get_available_providers(config)

        if not available['openai'] and not available['gemini']:
            result['error'] = ('No AI provider is configured. Add OPENAI_API_KEY or '
                               'GEMINI_API_KEY to your .env file to activate the assistant.')
            result['reply'] = ("The AI assistant isn't connected yet. Please add an OpenAI or "
                               "Gemini API key in your .env file to enable replies.")
            result['model_used'] = 'unavailable'
            return result

        if chatbot_config is None:
            result['error'] = 'Chatbot configuration is missing or inactive'
            return result

        # Purpose-based provider preferences (fall back to sensible defaults)
        text_pref = (getattr(chatbot_config, 'text_provider', 'auto') or 'auto')
        image_pref = (getattr(chatbot_config, 'image_provider', 'auto') or 'auto')
        openai_model = getattr(chatbot_config, 'openai_model', '') or 'gpt-4o'
        gemini_model = getattr(chatbot_config, 'gemini_model', '') or GEMINI_FLASH_MODEL

        page_profile = _get_page_profile(integration) if integration else None
        if page_profile:
            result['checkout_link'] = page_profile.checkout_link or ''

        # ── Image Recognition ─────────────────────────────────────────────────
        if input_type == 'image':
            result['intent'] = force_intent or 'general_query'
            # For 'auto', prefer OpenAI (stronger vision) then Gemini.
            provider, fell_back = _resolve_provider(image_pref, available, auto_order=['openai', 'gemini'])
            if provider is None:
                result['error'] = ('Image recognition is unavailable — no AI provider key is '
                                   'configured for it.')
                result['reply'] = ("Sorry, I can't read images right now — image recognition "
                                   "hasn't been set up yet.")
                result['model_used'] = 'unavailable'
                return result

            system_prompt = build_dynamic_prompt(chatbot_config, page_profile, result['intent'])
            if provider == 'openai':
                result['model_used'] = f"{openai_model} (vision)"
                result['reply'] = _call_openai_vision(
                    message_text, system_prompt, openai_key, config=chatbot_config,
                    model=openai_model, image_b64=image_data, mime_type=image_mime)
            else:
                # Gemini vision uses Flash (Pro has 0 free-tier quota); label honestly.
                vision_model = GEMINI_FLASH_MODEL if 'pro' in (gemini_model or '') else gemini_model
                result['model_used'] = f"{vision_model} (vision)"
                result['reply'] = _call_gemini_vision(
                    message_text, system_prompt, gemini_key, config=chatbot_config,
                    model=gemini_model, image_b64=image_data, mime_type=image_mime)
            if fell_back:
                result['notice'] = (f"'{image_pref}' isn't configured for image recognition — "
                                    f"used {provider} instead.")
            result['success'] = True
            return result

        # ── Audio (stub) ──────────────────────────────────────────────────────
        if input_type == 'audio':
            result['intent'] = force_intent or 'general_query'
            result['model_used'] = 'gemini-audio'
            result['reply'] = "[Audio processing via Gemini — transcription pending integration]"
            result['success'] = True
            return result

        # ── Text Messages ─────────────────────────────────────────────────────
        intent = force_intent if force_intent else classify_intent(message_text, config)
        result['intent'] = intent

        # Human Handoff / Ticketing logic
        triggers = getattr(chatbot_config, 'handoff_triggers', [])
        if triggers and isinstance(triggers, list):
            lower_msg = message_text.lower()
            for trigger in triggers:
                if trigger.lower() in lower_msg:
                    result['open_ticket'] = True
                    break

        system_prompt = build_dynamic_prompt(chatbot_config, page_profile, intent)

        if text_pref in ('openai', 'gemini'):
            # ── Explicit provider chosen by the operator ──────────────────────
            provider, fell_back = _resolve_provider(text_pref, available)
            if provider is None:
                result['error'] = 'No AI provider is available for text replies.'
                result['reply'] = ("The AI assistant isn't connected yet. Please add an API key "
                                   "in your .env file.")
                result['model_used'] = 'unavailable'
                return result
            model_name = openai_model if provider == 'openai' else gemini_model
            reply, model_label = _gen_text(provider, model_name, message_text, system_prompt,
                                           config, chatbot_config)
            result['reply'] = reply
            result['model_used'] = model_label
            if fell_back:
                result['notice'] = (f"'{text_pref}' isn't configured — replied using {provider} "
                                    f"instead.")
            result['success'] = True
            return result

        # ── Auto (smart, intent-based) routing ────────────────────────────────
        use_premium_model = intent in ('purchase_intent', 'product_issue')

        if intent == 'spam_noise':
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

        if use_premium_model:
            if openai_key:
                result['model_used'] = 'gpt-4o'
                result['reply'] = _call_openai_chat(message_text, system_prompt, openai_key, config=chatbot_config)
            elif gemini_key:
                result['model_used'] = 'gemini-pro'
                result['reply'] = _call_gemini_pro(message_text, system_prompt, gemini_key, chatbot_config)
        else:
            if gemini_key:
                result['model_used'] = 'gemini-flash'
                result['reply'] = _call_gemini_flash(message_text, system_prompt, gemini_key, chatbot_config)
            elif openai_key:
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
    """Calls Gemini Flash — low cost, high speed. For FAQs and spam."""
    temperature = float(getattr(config, 'creativity_level', '0.7')) if config else 0.7
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    return _call_gemini_rest(GEMINI_FLASH_MODEL, system_prompt, user_message, api_key,
                              temperature=temperature, max_tokens=max_tokens)


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


def _call_openai_vision(prompt_text: str, system_prompt: str, api_key: str, config=None,
                        model: str = 'gpt-4o', image_b64: str = None, mime_type: str = None) -> str:
    """
    Calls OpenAI Vision (GPT-4o family) for image analysis — OCR, product labels,
    screenshots, barcodes.

    When image_b64 is provided, the image is sent as a real multimodal message via a
    data-URI image_url block so the model actually sees it. If no image bytes are
    supplied, falls back to a text-only description (legacy callers).
    """
    client = _get_openai_client(api_key=api_key)

    temp = float(getattr(config, 'creativity_level', '0.7')) if config else 0.5
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    model = (model or 'gpt-4o').strip()

    if image_b64:
        data_uri = f"data:{mime_type or 'image/jpeg'};base64,{image_b64}"
        user_content = [
            {'type': 'text', 'text': prompt_text or "Describe this image and read any text in it."},
            {'type': 'image_url', 'image_url': {'url': data_uri}},
        ]
    else:
        user_content = f"The customer sent an image. Description/context: {prompt_text}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ],
        max_tokens=max_tokens,
        temperature=temp,
    )
    return response.choices[0].message.content.strip()


def _call_gemini_pro(user_message: str, system_prompt: str, api_key: str, config=None) -> str:
    """
    Calls Gemini Pro — premium model. For complex reasoning and sales when OpenAI is missing.
    Free-tier Gemini keys get a 0 request/day quota for Pro models, so on any failure
    (quota, availability) this falls back to Flash rather than dropping the reply entirely.
    """
    temperature = float(getattr(config, 'creativity_level', '0.7')) if config else 0.7
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    try:
        return _call_gemini_rest(GEMINI_PRO_MODEL, system_prompt, user_message, api_key,
                                  temperature=temperature, max_tokens=max_tokens)
    except Exception as e:
        logger.warning(f"Gemini Pro call failed ({e}), falling back to Gemini Flash.")
        return _call_gemini_rest(GEMINI_FLASH_MODEL, system_prompt, user_message, api_key,
                                  temperature=temperature, max_tokens=max_tokens)


def _call_gemini_vision(prompt_text: str, system_prompt: str, api_key: str, config=None,
                        model: str = None, image_b64: str = None, mime_type: str = None) -> str:
    """
    Calls Gemini (multimodal) for image analysis.

    When image_b64 is provided, the actual image is sent inline so the model reads it.
    If no image bytes are supplied, falls back to a text-only description (legacy callers).
    """
    temperature = float(getattr(config, 'creativity_level', '0.7')) if config else 0.5
    max_tokens = int(getattr(config, 'response_length', '500')) if config else 500
    model = (model or GEMINI_FLASH_MODEL).strip()
    # Pro models have a 0-quota on free-tier keys; use Flash for vision unless told otherwise.
    if 'pro' in model:
        model = GEMINI_FLASH_MODEL

    if image_b64:
        prompt = prompt_text or "Describe this image and read any text in it."
        return _call_gemini_vision_rest(model, system_prompt, prompt, image_b64, mime_type,
                                        api_key, temperature=temperature, max_tokens=max_tokens)

    user_message = f"The customer sent an image. Description/context: {prompt_text}"
    return _call_gemini_rest(model, system_prompt, user_message, api_key,
                              temperature=temperature, max_tokens=max_tokens)


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
    from .meta_sync import reply_to_meta_comment, send_meta_message, record_outbound_dm

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
            # reply_to_meta_comment returns (success, new_comment_id) — must unpack,
            # a raw non-empty tuple is always truthy and would mask API failures.
            success, new_reply_id = reply_to_meta_comment(
                integration, comment.meta_comment_id, public_reply_text
            )
            if success:
                result['public_reply_sent'] = True
                logger.info(f"Public reply sent to comment {comment.meta_comment_id}")
                # Record our own reply locally so the next page sync doesn't
                # re-import it as a brand-new top-level comment (which would loop).
                if new_reply_id:
                    from django.utils import timezone as _tz
                    from .models import CRMSocialComment
                    page_name = integration.parsed_name or integration.account_name or 'Page'
                    CRMSocialComment.objects.get_or_create(
                        meta_comment_id=new_reply_id,
                        defaults={
                            'post': comment.post,
                            'parent_comment': comment,
                            'sender_name': page_name,
                            'sender_id': integration.parsed_id,
                            'message': public_reply_text,
                            'created_time': _tz.now(),
                        }
                    )
            else:
                logger.warning(f"Public reply failed to post for comment {comment.meta_comment_id}")
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
                    # Persist it into the CRM inbox so it shows up on the
                    # Conversations page, same as the keyword-automation funnel does.
                    try:
                        record_outbound_dm(integration, comment, dm_text)
                    except Exception:
                        logger.exception("record_outbound_dm failed for Comment-to-DM funnel")
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
