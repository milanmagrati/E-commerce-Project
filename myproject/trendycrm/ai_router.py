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
import time
import requests

from . import triage

logger = logging.getLogger(__name__)

# Google's newer Gemini models are "thinking" models by default and will burn the
# whole max_output_tokens budget on hidden reasoning tokens before ever emitting
# visible text unless thinking is turned down — see _call_gemini_rest. A budget of
# 0 used to mean "disable thinking" but the model behind the gemini-flash-latest
# alias now rejects 0 with INVALID_ARGUMENT, so 1 (minimum non-zero) is used instead.
GEMINI_THINKING_BUDGET = 1

# Longest wait we'll honour from a 429's retryDelay before giving up. Auto-replies
# run in a background thread, so a short pause costs nothing the customer sees.
GEMINI_MAX_RETRY_WAIT = 65

# Google answers 503 UNAVAILABLE ("high demand") when a shared model is momentarily
# overloaded, and simply stops responding when the request stalls. Neither is a quota
# cap — both clear in seconds, so retrying the same model recovers the reply, and it
# has to be retried *here*, because cross-provider failover only helps when a second
# provider key is configured.
GEMINI_OVERLOAD_STATUSES = (500, 502, 503, 504)
GEMINI_RETRY_BACKOFF = (2, 5)  # seconds to wait before each retry

# (connect, read) timeouts. An unreachable host should fail fast; a model that has
# accepted the request deserves room to finish thinking before we give up on it.
GEMINI_TIMEOUT = (10, 45)
GEMINI_VISION_TIMEOUT = (10, 60)

# Ceiling on total time spent inside one _post_gemini call. A read timeout burns the
# full read budget before we even start backing off, so without this the retry
# schedule could pin an auto-reply thread for minutes.
GEMINI_TOTAL_DEADLINE = 100
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
                       temperature: float = 0.7, max_tokens: int = 500,
                       response_schema: dict = None, thinking_budget: int = None) -> str:
    """
    Calls the Gemini REST API directly instead of the google-generativeai SDK.

    The SDK depends on grpc, whose compiled extension (cygrpc) gets blocked by
    Windows Application Control Policy on this host — the REST API has no such
    dependency and uses the exact same backend, so this is a drop-in replacement.

    `response_schema` switches the model into constrained JSON decoding, which
    makes it structurally incapable of answering with prose. Used by the intent
    classifier, where free-form output was leaking fragments of the prompt.
    """
    if not api_key or api_key == 'your-gemini-api-key-here':
        raise ValueError("Gemini API key not configured.")

    url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={api_key}"
    budget = GEMINI_THINKING_BUDGET if thinking_budget is None else thinking_budget
    payload = {
        'contents': [{'parts': [{'text': user_message}]}],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
            'thinkingConfig': {'thinkingBudget': budget},
        },
    }
    if response_schema:
        payload['generationConfig']['responseMimeType'] = 'application/json'
        payload['generationConfig']['responseSchema'] = response_schema
    if system_prompt:
        payload['systemInstruction'] = {'parts': [{'text': system_prompt}]}

    response = _post_gemini(url, payload)

    if not response.ok:
        raise ValueError(f"Gemini API error {response.status_code}: {_extract_gemini_error(response)}")
    data = response.json()

    candidates = data.get('candidates') or []
    if not candidates:
        block_reason = data.get('promptFeedback', {}).get('blockReason', 'unknown')
        raise ValueError(f"Gemini returned no candidates (blockReason={block_reason})")

    parts = candidates[0].get('content', {}).get('parts', [])
    # Skip parts the API flags as hidden reasoning — they carry `text` too, and
    # concatenating them corrupts the visible answer.
    text = ''.join(p.get('text', '') for p in parts if 'text' in p and not p.get('thought')).strip()
    if not text:
        raise ValueError(f"Gemini returned empty text (finishReason={candidates[0].get('finishReason')})")
    return text


def _post_gemini(url, payload, timeout=None):
    """
    POSTs to Gemini, absorbing the failure modes that resolve on their own:

      • 429 rate limit — free-tier keys throttle constantly and Google tells us
        exactly how long to wait. Honouring that once turns a dropped customer
        reply into a slightly slower one. Retried once, only when the wait is short.
      • 5xx overload — the backend is momentarily out of capacity.
      • Timeouts / dropped connections — the request never got an answer at all.
        These surface as exceptions rather than status codes, so they have to be
        caught here or they escape past every retry we've set up.

    The last two share a short fixed backoff, bounded by GEMINI_TOTAL_DEADLINE so a
    stalling model can't pin the thread. Auto-replies run in a background thread, so
    these pauses cost nothing the customer sees.

    Returns the final response (the caller checks .ok), or re-raises the transport
    error if no attempt ever reached the API.
    """
    timeout = GEMINI_TIMEOUT if timeout is None else timeout
    # What the next attempt could cost us in the worst case — a stalled request burns
    # its whole read budget, so the deadline has to account for it *before* retrying,
    # not after.
    read_budget = timeout[1] if isinstance(timeout, (tuple, list)) else timeout
    started = time.monotonic()
    response = None
    transport_error = None

    for attempt in range(1 + len(GEMINI_RETRY_BACKOFF)):
        if attempt:
            wait = GEMINI_RETRY_BACKOFF[attempt - 1]
            reason = transport_error or f"HTTP {response.status_code}"
            if time.monotonic() - started + wait + read_budget > GEMINI_TOTAL_DEADLINE:
                logger.warning(f"Gemini still failing ({reason}); retry budget exhausted")
                break
            logger.warning(f"Gemini unavailable ({reason}); retrying in {wait}s")
            time.sleep(wait)

        try:
            response, transport_error = requests.post(url, json=payload, timeout=timeout), None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            response, transport_error = None, f"{type(exc).__name__}"
            continue

        if response.status_code == 429 and not attempt:
            wait = _gemini_retry_delay(response)
            if wait and wait <= GEMINI_MAX_RETRY_WAIT:
                logger.warning(f"Gemini rate limited; retrying once in {wait:.0f}s")
                time.sleep(wait)
                try:
                    response = requests.post(url, json=payload, timeout=timeout)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    response, transport_error = None, f"{type(exc).__name__}"
                    continue

        if response.status_code not in GEMINI_OVERLOAD_STATUSES:
            return response

    if response is None:
        # Every attempt failed before reaching Google. Report it as a provider error
        # so _is_transient_provider_error routes it to failover like any other.
        raise ValueError(f"Gemini unreachable: {transport_error} after "
                         f"{1 + len(GEMINI_RETRY_BACKOFF)} attempts")
    return response


def _gemini_retry_delay(response):
    """
    Pulls Google's suggested wait out of a 429 body ('retryDelay': '29s').
    Returns seconds as a float, or None when retrying would be pointless.

    A per-*minute* throttle clears on its own in under a minute, so waiting is
    worth it. A per-*day* cap does not, and Google still sends a ~30-60s
    retryDelay for it — sleeping on that just stalls the thread and fails again,
    so those are reported immediately instead.
    """
    try:
        error = response.json().get('error', {})
        details = error.get('details', [])

        for detail in details:
            for violation in detail.get('violations', []):
                if 'PerDay' in str(violation.get('quotaId', '')):
                    return None

        for detail in details:
            delay = detail.get('retryDelay')
            if delay:
                return float(str(delay).rstrip('s')) + 1  # +1s of headroom
    except (ValueError, TypeError, AttributeError):
        pass
    return None


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
            'thinkingConfig': {'thinkingBudget': GEMINI_THINKING_BUDGET},
        },
    }
    if system_prompt:
        payload['systemInstruction'] = {'parts': [{'text': system_prompt}]}

    response = _post_gemini(url, payload, timeout=GEMINI_VISION_TIMEOUT)
    if not response.ok:
        raise ValueError(f"Gemini vision API error {response.status_code}: {_extract_gemini_error(response)}")
    data = response.json()

    candidates = data.get('candidates') or []
    if not candidates:
        block_reason = data.get('promptFeedback', {}).get('blockReason', 'unknown')
        raise ValueError(f"Gemini vision returned no candidates (blockReason={block_reason})")

    parts = candidates[0].get('content', {}).get('parts', [])
    # Skip parts the API flags as hidden reasoning — they carry `text` too, and
    # concatenating them corrupts the visible answer.
    text = ''.join(p.get('text', '') for p in parts if 'text' in p and not p.get('thought')).strip()
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


# ─── Response length ──────────────────────────────────────────────────────────
# CRMChatbotConfig.response_length stores '100' / '500' / '1500'. Those numbers were
# being passed straight through as maxOutputTokens, which is a hard cut-off — the
# model gets guillotined mid-sentence ("Short & Punchy" produced replies like
# "Yes, we"). Length is a *style* instruction; the token budget only needs to be
# generous enough that the model can finish the thought it planned.
LENGTH_PROFILES = {
    '100': (400, 'Keep your reply very short — 1-2 sentences, no preamble.'),
    '500': (900, 'Keep your reply concise — at most a short paragraph.'),
    '1500': (2200, 'You may answer in detail, but stay organised and skimmable.'),
}
DEFAULT_LENGTH_PROFILE = LENGTH_PROFILES['500']


def _length_profile(config):
    """(max_tokens, style_instruction) for a chatbot config's response_length."""
    if not config:
        return DEFAULT_LENGTH_PROFILE
    return LENGTH_PROFILES.get(str(getattr(config, 'response_length', '500')), DEFAULT_LENGTH_PROFILE)


def _max_tokens(config):
    return _length_profile(config)[0]


def _temperature(config, default=0.7):
    """Creativity level as a float, tolerant of a blank/corrupt stored value."""
    if not config:
        return default
    try:
        return float(getattr(config, 'creativity_level', default) or default)
    except (TypeError, ValueError):
        return default


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


def _is_transient_provider_error(exc) -> bool:
    """
    True for failures that the *other* provider might not have: quota/rate limits
    and upstream overload. Auth errors and bad requests are excluded — retrying
    those elsewhere just produces a second failure.
    """
    detail = str(exc).lower()
    return any(s in detail for s in (
        'resource_exhausted', '429', 'rate limit', 'quota', 'overloaded',
        'unavailable', '503', 'timed out', 'timeout',
        'unreachable', 'connectionerror', 'connection aborted',
    ))


def _gen_text_with_failover(provider: str, model_name: str, user_message: str,
                            system_prompt: str, config: dict, chatbot_config,
                            available: dict):
    """
    Generates a reply, transparently switching to the other configured provider
    when the first is rate limited or out of quota.

    Free-tier keys hit daily caps, and when that happened the bot simply went
    silent on real customers. If a second provider is configured, use it.

    Returns (reply, model_label, notice).
    """
    try:
        reply, label = _gen_text(provider, model_name, user_message, system_prompt,
                                 config, chatbot_config)
        return reply, label, None
    except Exception as exc:
        other = 'openai' if provider == 'gemini' else 'gemini'
        if not (_is_transient_provider_error(exc) and available.get(other)):
            raise
        logger.warning(f"{provider} unavailable ({exc}); retrying on {other}")
        other_model = (
            (getattr(chatbot_config, 'openai_model', '') or 'gpt-4o')
            if other == 'openai'
            else (getattr(chatbot_config, 'gemini_model', '') or GEMINI_FLASH_MODEL)
        )
        reply, label = _gen_text(other, other_model, user_message, system_prompt,
                                 config, chatbot_config)
        return reply, label, f"{provider} was rate limited — replied using {other} instead."


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
    temperature = _temperature(chatbot_config)
    max_tokens = _max_tokens(chatbot_config)
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
def _intent_instruction(chatbot_config, intent: str) -> str:
    """
    How the AI should answer this kind of message.

    Three layers, most specific first: whatever the operator typed into the
    rule's "AI reply" box, the built-in mode for one of the original types, and
    — for a message type this business invented — a line generated from the
    rule itself, so a custom bucket still steers the reply instead of silently
    falling back to the generic FAQ mode.
    """
    rules = triage.rules_for(chatbot_config)
    rule = triage.rule_by_key(rules, intent)

    if rule and rule.get('instruction'):
        return f"\n--- {rule['label'].upper()} MODE ACTIVE ---\n{rule['instruction']}"
    if intent in triage.BUILTIN_INSTRUCTIONS:
        return triage.BUILTIN_INSTRUCTIONS[intent]
    if rule:
        return (
            f"\n--- {rule['label'].upper()} MODE ACTIVE ---\n"
            f"This business files messages like this one under \"{rule['label']}\" "
            f"(priority: {rule['priority']}).\n"
            "Answer it directly and helpfully in 1-3 sentences, using the business "
            "information above. Do not guess at anything you haven't been told."
        )
    return triage.BUILTIN_INSTRUCTIONS[triage.FALLBACK_KEY]


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
    if chatbot_config.business_email:
        system_parts.append(f"Support email: {chatbot_config.business_email}")
    if chatbot_config.business_phone:
        system_parts.append(f"Support phone: {chatbot_config.business_phone}")
    if getattr(chatbot_config, 'business_address', None):
        system_parts.append(f"Business address: {chatbot_config.business_address}")

    cities = [c for c in (getattr(chatbot_config, 'cities_served', None) or []) if c]
    if cities:
        # Spelled out as an exhaustive list so the model answers "do you deliver
        # to X?" from the operator's coverage instead of guessing.
        system_parts.append(
            "Cities/areas we serve and deliver to: " + ", ".join(cities) +
            ". If a customer asks about anywhere not on this list, say we don't "
            "currently cover it and offer to check with the team — never assume we do."
        )

    socials = [
        (label, getattr(chatbot_config, field, None))
        for label, field in (
            ('Facebook', 'social_facebook'),
            ('Instagram', 'social_instagram'),
            ('TikTok', 'social_tiktok'),
            ('WhatsApp', 'social_whatsapp'),
        )
    ]
    socials = [f"{label}: {value}" for label, value in socials if value]
    if socials:
        system_parts.append(
            "Our official channels (share these only when asked, exactly as written): "
            + " | ".join(socials)
        )

    # ── Business Knowledge Base ───────────────────────────────────────────────
    # Every section the operator fills in on the Chatbot page must reach the model —
    # otherwise the Offerings/FAQs/Playbook tabs are just a text editor that does nothing.
    response_tone_map = {
        'professional_friendly': 'Sound professional but warm and approachable.',
        'formal': 'Sound formal, precise, and polite. Avoid slang and emojis.',
        'casual': 'Sound casual and relaxed, like a friendly shopkeeper.',
        'energetic': 'Sound energetic and enthusiastic. Use upbeat language.',
        'empathetic': 'Sound empathetic and supportive. Acknowledge feelings first.',
    }
    tone_setting = response_tone_map.get(getattr(chatbot_config, 'response_tone', ''), '')
    if tone_setting:
        system_parts.append(f"\nHOUSE TONE: {tone_setting}")

    if chatbot_config.tone_voice:
        system_parts.append(
            "\n--- BRAND VOICE (follow this closely) ---\n" + chatbot_config.tone_voice
        )
    if chatbot_config.offerings:
        system_parts.append(
            "\n--- PRODUCTS & SERVICES WE SELL ---\n" + chatbot_config.offerings +
            "\nOnly recommend products from this list. Never invent products or prices."
        )
    if chatbot_config.faq_text:
        system_parts.append(
            "\n--- FREQUENTLY ASKED QUESTIONS (use these answers verbatim where they fit) ---\n"
            + chatbot_config.faq_text
        )
    if chatbot_config.playbook:
        system_parts.append(
            "\n--- SALES & SUPPORT PLAYBOOK (internal rules — never quote these to the customer) ---\n"
            + chatbot_config.playbook
        )

    if getattr(chatbot_config, 'primary_language', 'auto') != 'auto':
        # 'ne' is romanized Nepali — that's what the Language picker promises,
        # and what customers actually type on Messenger/WhatsApp.
        lang_map = {
            'en': 'English', 'es': 'Spanish', 'fr': 'French',
            'ne': 'romanized Nepali (Nepali written in the Latin alphabet, not Devanagari)',
            'hi': 'Hindi',
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

    # Intent-based behavioral rules. The modes themselves are the built-in
    # instructions in triage.py; a business can override any of them, or write
    # one for a message type it invented, from the Message Triage Rules panel.
    system_parts.append(_intent_instruction(chatbot_config, intent))

    # Length is enforced by instruction, not by truncating the model mid-sentence.
    system_parts.append(f"\nLENGTH: {_length_profile(chatbot_config)[1]}")
    return "\n".join(p for p in system_parts if p)


# ─── Step 1: Intent Classifier ────────────────────────────────────────────────
# The buckets a message can land in — their keywords, chip labels and priorities
# — are per business now and live in trendycrm/triage.py, where an operator
# edits them from the Chatbot page's "Message Triage Rules" panel. Every
# function below therefore asks for the rule list rather than closing over a
# fixed set of names.

# Whether to spend a second API call on LLM intent classification for messages
# the keyword pass can't settle. Off by default: on a free-tier key it halves how
# many customers can be answered before the quota runs out.
LLM_INTENT_CLASSIFIER = env_config('CRM_LLM_INTENT_CLASSIFIER', default=False, cast=bool)


def _intent_schema(intent_keys):
    """
    Constrained-decoding schema: with this set, Gemini can only emit one of the
    business's own intent keys. Free-form output used to leak fragments of the
    prompt itself (e.g. ',_emojis,_etc.'), which silently degraded every message
    to general_query.
    """
    return {
        'type': 'OBJECT',
        'properties': {'intent': {'type': 'STRING', 'enum': list(intent_keys)}},
        'required': ['intent'],
    }

# The keyword lists that used to live here (_ISSUE_KEYWORDS, _PURCHASE_KEYWORDS,
# _ORDER_STATUS_KEYWORDS, _DELIVERY_KEYWORDS, _GREETING_PHRASES, _CLOSING_PHRASES)
# are now the `keywords` of the built-in rules in trendycrm/triage.py, so an
# operator edits them per business from the Chatbot page instead of a developer
# editing them per deploy. Their meaning is unchanged: unambiguous signals in
# English and romanized Nepali, checked before spending an API call, and reused
# as the fallback when the model's answer is unusable — a refund complaint must
# never silently land in general_query or spam_noise.


def _keyword_intent(message_text: str, rules=None) -> str:
    """
    Deterministic first pass. Returns an intent, or '' when nothing is conclusive
    and the message should go to the model.

    `rules` is the business's triage rule list; without it the built-in set is
    used. The one-argument form is what the webhook path, the backfill command
    and the tests call, and it must keep behaving exactly as it did.
    """
    return triage.classify(message_text, rules)[0]


def _classifier_categories(rules) -> str:
    """
    The category list handed to the LLM classifier, written from the business's
    own rules so a custom type is classifiable instead of being squeezed into
    one of the built-in eight. A rule describes itself by its keywords when the
    operator wrote no description for it.
    """
    lines = []
    for rule in rules:
        if not rule.get('enabled', True):
            continue
        description = triage.BUILTIN_DESCRIPTIONS.get(rule['key'], '')
        if not description:
            examples = ', '.join(f'"{k}"' for k in (rule.get('keywords') or [])[:6])
            description = (
                f"messages the business files under \"{rule['label']}\""
                + (f" — e.g. {examples}." if examples else ".")
            )
        lines.append(f"- {rule['key']}: {description}")
    return '\n'.join(lines)


def classify_intent(message_text: str, config: dict, chatbot_config=None) -> str:
    """
    Classifies a message into one of the business's triage rules.

    Runs a deterministic keyword pass first, then — only if LLM_INTENT_CLASSIFIER
    is on — asks Gemini Flash (with a constrained enum schema) or OpenAI
    GPT-4o-mini about anything ambiguous. Falls back to the keyword verdict,
    never blindly to 'general_query', so a model hiccup can't turn a refund
    request into noise.

    `chatbot_config` selects whose rules apply; without it the built-in set is
    used, which is what the standalone test scripts rely on.
    """
    rules = triage.rules_for(chatbot_config)
    valid_intents = [r['key'] for r in rules]

    keyword_verdict = _keyword_intent(message_text, rules)
    if keyword_verdict:
        return keyword_verdict

    fallback = triage.FALLBACK_KEY

    # Classification is a *second* API call on every message that the keyword
    # pass can't settle, which doubles quota use per customer message. On a
    # free-tier key that is the difference between answering customers and
    # hitting 429s, and the reply prompt already adapts to what's being asked.
    # Turn this on when the key has real quota behind it.
    if not LLM_INTENT_CLASSIFIER:
        return fallback

    classification_prompt = f"""You are a message intent classifier for an e-commerce business.
Classify the following customer message into EXACTLY ONE category:
{_classifier_categories(rules)}

Customer message: "{message_text}"
"""

    gemini_key = config.get('gemini_api_key', '')
    openai_key = config.get('openai_api_key', '')

    try:
        response_text = ""
        if gemini_key:
            # Gemini spends hidden reasoning tokens out of this same budget, so a
            # tight cap leaves nothing for the answer. The enum schema guarantees
            # the shape; the budget just has to be big enough to emit it.
            response_text = _call_gemini_rest(
                GEMINI_FLASH_MODEL, None, classification_prompt, gemini_key,
                temperature=0.0, max_tokens=256, response_schema=_intent_schema(valid_intents),
                thinking_budget=0,
            )
        elif openai_key:
            client = _get_openai_client(api_key=openai_key)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[{'role': 'user', 'content': classification_prompt}],
                max_tokens=32,
                temperature=0.0,
            )
            response_text = response.choices[0].message.content
        else:
            logger.warning("No API keys configured for intent classification.")
            return fallback

        raw = (response_text or '').strip()

        # Constrained decoding gives us {"intent": "..."}; the OpenAI branch and
        # any future non-schema path give a bare label.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw = str(parsed.get('intent', ''))
        except (ValueError, TypeError):
            pass

        intent = raw.strip().strip('"\'.').lower().replace(' ', '_').replace('-', '_')
        if intent in valid_intents:
            return intent
        # Models sometimes wrap the label in a sentence ("Category: purchase_intent.")
        for v in valid_intents:
            if v in intent:
                return v

        logger.warning(
            f"Intent classifier returned unexpected value: '{intent[:80]}'. "
            f"Falling back to '{fallback}'."
        )
        return fallback

    except Exception as e:
        logger.error(f"Intent classification failed: {e}. Falling back to '{fallback}'.")
        return fallback


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
        'error_kind': str,      # '' | 'rate_limit' | 'provider_down' | 'not_configured' | 'other'
        'retryable': bool,      # True when the same call may succeed shortly
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
        # Callers need to tell "wait and it will work" apart from "this will fail
        # identically forever" — one deserves an automatic retry, the other only
        # wastes quota and leaves a warning up longer.
        'error_kind': '',
        'retryable': False,
    }

    try:
        config = _get_ai_config()
        openai_key = config['openai_api_key']
        gemini_key = config['gemini_api_key']
        available = _get_available_providers(config)

        if not available['openai'] and not available['gemini']:
            result['error'] = ('No AI provider is configured. Add OPENAI_API_KEY or '
                               'GEMINI_API_KEY to your .env file to activate the assistant.')
            result['error_kind'] = 'not_configured'
            result['reply'] = ("The AI assistant isn't connected yet. Please add an OpenAI or "
                               "Gemini API key in your .env file to enable replies.")
            result['model_used'] = 'unavailable'
            return result

        if chatbot_config is None:
            result['error'] = 'Chatbot configuration is missing or inactive'
            result['error_kind'] = 'not_configured'
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
                result['error_kind'] = 'not_configured'
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
        intent = force_intent if force_intent else classify_intent(message_text, config, chatbot_config)
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
                result['error_kind'] = 'not_configured'
                result['reply'] = ("The AI assistant isn't connected yet. Please add an API key "
                                   "in your .env file.")
                result['model_used'] = 'unavailable'
                return result
            model_name = openai_model if provider == 'openai' else gemini_model
            reply, model_label, notice = _gen_text_with_failover(
                provider, model_name, message_text, system_prompt,
                config, chatbot_config, available)
            result['reply'] = reply
            result['model_used'] = model_label
            if fell_back:
                result['notice'] = (f"'{text_pref}' isn't configured — replied using {provider} "
                                    f"instead.")
            elif notice:
                result['notice'] = notice
            result['success'] = True
            return result

        # ── Auto (smart, intent-based) routing ────────────────────────────────
        # Pick the provider and model this intent deserves, then generate with
        # failover so a quota-exhausted provider doesn't silence the bot.
        if intent == 'spam_noise':
            # Cheapest possible model — these barely deserve a reply.
            provider = 'gemini' if gemini_key else ('openai' if openai_key else None)
            auto_model = GEMINI_FLASH_MODEL if provider == 'gemini' else 'gpt-4o-mini'
        elif intent in ('purchase_intent', 'product_issue'):
            # Highest-value conversations get the strongest available model.
            provider = 'openai' if openai_key else ('gemini' if gemini_key else None)
            auto_model = 'gpt-4o' if provider == 'openai' else GEMINI_PRO_MODEL
        else:
            provider = 'gemini' if gemini_key else ('openai' if openai_key else None)
            auto_model = GEMINI_FLASH_MODEL if provider == 'gemini' else 'gpt-4o-mini'

        if provider is None:
            result['reply'] = "Thanks for your message!"
            result['model_used'] = 'unavailable'
            result['success'] = True
            return result

        reply, model_label, notice = _gen_text_with_failover(
            provider, auto_model, message_text, system_prompt,
            config, chatbot_config, available)
        result['reply'] = reply
        result['model_used'] = model_label
        if notice:
            result['notice'] = notice
        result['success'] = True

    except Exception as e:
        logger.exception(f"AI Router failed: {e}")
        detail = str(e)
        # Free-tier Gemini/OpenAI keys are rate limited per minute; the raw
        # RESOURCE_EXHAUSTED blob tells the operator nothing actionable.
        if 'RESOURCE_EXHAUSTED' in detail or '429' in detail or 'rate limit' in detail.lower():
            result['error'] = ('AI provider rate limit reached — your API key has used its quota '
                               'for the moment. Wait a minute and try again, or upgrade the plan. '
                               f'({detail[:180]})')
            result['error_kind'] = 'rate_limit'
            result['retryable'] = True
        elif any(s in detail.lower() for s in ('unavailable', '503', 'overload',
                                               'timed out', 'timeout', 'unreachable')):
            # Provider-side capacity or a network stall — not anything the operator
            # did wrong. Already retried a few times by this point, so say so rather
            # than just "try again".
            result['error'] = ('AI provider was overloaded or unreachable and stayed that way '
                               'through several retries. This normally clears within a minute — '
                               'resend the reply then, or configure a second provider key so the '
                               f'bot can fail over automatically. ({detail[:180]})')
            result['error_kind'] = 'provider_down'
            result['retryable'] = True
        else:
            result['error'] = detail
            result['error_kind'] = 'other'
        result['reply'] = "I'm sorry, I'm having trouble processing your request right now. Please try again shortly."

    return result


# ─── Model Callers ────────────────────────────────────────────────────────────
def _call_gemini_flash(user_message: str, system_prompt: str, api_key: str, config=None) -> str:
    """Calls Gemini Flash — low cost, high speed. For FAQs and spam."""
    temperature = _temperature(config)
    max_tokens = _max_tokens(config)
    return _call_gemini_rest(GEMINI_FLASH_MODEL, system_prompt, user_message, api_key,
                              temperature=temperature, max_tokens=max_tokens)


def _call_openai_chat(user_message: str, system_prompt: str, api_key: str, model: str = 'gpt-4o', config=None) -> str:
    """Calls OpenAI Chat API. Defaults to gpt-4o, but can accept gpt-4o-mini for cost-savings."""
    client = _get_openai_client(api_key=api_key)
    
    temp = _temperature(config)
    max_tokens = _max_tokens(config)
    
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

    temp = _temperature(config, default=0.5)
    max_tokens = _max_tokens(config)
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
    temperature = _temperature(config)
    max_tokens = _max_tokens(config)
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
    temperature = _temperature(config, default=0.5)
    max_tokens = _max_tokens(config)
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

                dm_sent, dm_send_error = send_meta_message(integration, sender_id, dm_text)
                if dm_sent:
                    result['dm_sent'] = True
                    logger.info(f"DM sent to {sender_id} (intent={intent}, model={result['model_used']})")
                    from .views import charge_ai_credits
                    charge_ai_credits(
                        chatbot_config,
                        action='comment_dm',
                        model_used=result['model_used'],
                        description=f"Comment-to-DM: {message_text}",
                    )
                    # Persist it into the CRM inbox so it shows up on the
                    # Conversations page, same as the keyword-automation funnel does.
                    try:
                        record_outbound_dm(integration, comment, dm_text)
                    except Exception:
                        logger.exception("record_outbound_dm failed for Comment-to-DM funnel")
                else:
                    logger.warning(f"send_meta_message failed for sender_id={sender_id}: {dm_send_error}")

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
