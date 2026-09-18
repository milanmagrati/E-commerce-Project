"""
Message triage rules — what each kind of customer message is called, how urgent
it is, and how the AI should answer it.

These used to be three hardcoded things in three places: the keyword tuples in
``ai_router``, the chip labels in ``CRMMessage.INTENT_DISPLAY``, and the reply
modes in ``build_dynamic_prompt``. They are one list of rules here, and an
operator can edit that list per business from the Chatbot page.

A rule::

    {
        'key': 'purchase_intent',   # goes into CRMMessage.ai_intent
        'label': 'Purchase',        # the chip text an agent reads
        'priority': 'high',         # low | medium | high | urgent
        'match': 'contains',        # 'contains' | 'exact'  (see below)
        'keywords': ['how much', 'kati ho', ...],
        'alert': True,              # raise the agent alert chip/badge/popup
        'instruction': '',          # extra AI reply guidance ('' = built-in mode)
        'enabled': True,
        'locked': False,            # cannot be deleted (the fallback buckets)
    }

``match`` is the whole reason greetings work: 'contains' looks for the keyword
anywhere in the message, while 'exact' compares it against the message stripped
of punctuation and emoji. 'hi' is a substring of "this" and 'ok' of "broken", so
those short phrases can only ever be matched as a whole message.

Deliberately free of Django model imports at module level — both ``models.py``
and ``ai_router.py`` import this, and either direction would otherwise cycle.
"""
import re

PRIORITIES = ('low', 'medium', 'high', 'urgent')

# Priorities that put a chip on the customer's own bubble, and the dot colour
# each one renders with. Kept here so the panel, the thread and the CSS agree.
PRIORITY_COLORS = {
    'low': '#22c55e',
    'medium': '#eab308',
    'high': '#f97316',
    'urgent': '#ef4444',
}

# The fallback bucket: whatever no other rule claims lands here, so it must
# always exist. `spam_noise` is the other locked rule — an empty or
# punctuation-only message has to resolve somewhere too.
FALLBACK_KEY = 'general_query'
NOISE_KEY = 'spam_noise'
LOCKED_KEYS = (FALLBACK_KEY, NOISE_KEY)

# An event_kind is 32 chars and custom rules derive theirs as 'alert_' + key.
MAX_KEY_LENGTH = 24
MAX_LABEL_LENGTH = 40
MAX_RULES = 40
MAX_KEYWORDS_PER_RULE = 200
# Below this, a 'contains' keyword starts matching inside unrelated words.
MIN_CONTAINS_KEYWORD_LENGTH = 3


# ─── The built-in rule set ────────────────────────────────────────────────────
# Order is the classifier: the passes run from the intents that most need a
# human down to the ones that need nobody, and the first hit wins. Complaints
# outrank purchase words — "I paid for this and it broke" is an issue, not a
# sale. Order status sits after purchase so "book my order" stays a sale, and
# before delivery so "where is my order" isn't read as a shipping FAQ.
DEFAULT_RULES = [
    {
        'key': 'product_issue',
        'label': 'Issue',
        'priority': 'high',
        'match': 'contains',
        'alert': True,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'refund', 'money back', 'broken', 'damaged', 'damage', 'defective', 'faulty',
            'torn', 'ripped', 'stained', 'wrong item', 'wrong size', 'wrong product',
            'wrong colour', 'wrong color', 'not working', "doesn't work", 'complaint',
            'return it', 'want to return', 'never arrived', 'still not received',
            'not received', 'missing', 'cancel my order', 'poor quality', 'bad quality',
            'bigrityo', 'bigreko', 'phutyo', 'galat', 'paisa firta', 'firta', 'aayena',
        ],
    },
    {
        'key': 'purchase_intent',
        'label': 'Purchase',
        'priority': 'high',
        'match': 'contains',
        'alert': True,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'how much', 'price', 'cost', 'discount', 'order kasari', 'want to buy',
            'i want to order', 'place an order', 'buy this', 'in stock', 'cod',
            'cash on delivery', 'payment', 'esewa', 'khalti',
            'kati ho', 'kati parcha', 'kati parchha', 'kina', 'kinna', 'order garna',
            # Buying-interest phrasing that doesn't ask a question first ("I need
            # this product"). Multi-word only — a bare 'need'/'want'/'buy' would
            # also match "I don't need this".
            'i need this', 'i need it', 'i want to buy', 'i want this', 'send me',
            'book order', 'book my order', "i'll take it", 'ill take it',
            'reserve one', 'can i order', 'how do i order', 'malai chai',
            'malai yo chaiyo',
        ],
    },
    {
        'key': 'order_status',
        'label': 'Order Status',
        'priority': 'medium',
        'match': 'contains',
        'alert': False,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'where is my order', 'where is my parcel', 'order status', 'track my order',
            'tracking number', 'tracking id', 'tracking code', 'my order number',
            'my parcel', 'when will i get', 'when will i receive', 'when will it arrive',
            'has it shipped', 'has it been shipped', 'dispatched',
            'kaha pugyo', 'kahile aauxa', 'kahile aaucha', 'kahile aaunxa',
            'order kaha', 'pathaisakyo',
        ],
    },
    {
        'key': 'delivery_query',
        'label': 'Delivery',
        'priority': 'medium',
        'match': 'contains',
        'alert': False,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'delivery charge', 'delivery cost', 'delivery fee', 'delivery time',
            'delivery kati', 'shipping charge', 'shipping cost', 'how many days',
            'how long will', 'home delivery', 'inside valley', 'outside valley',
            'outside kathmandu', 'do you deliver', 'deliver to', 'delivery available',
            'kati din', 'delivery hunxa', 'delivery huncha', 'pathauna milxa',
        ],
    },
    {
        'key': 'greeting',
        'label': 'Greeting',
        'priority': 'low',
        'match': 'exact',
        'alert': False,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'hi', 'hii', 'hiii', 'hey', 'heyy', 'hello', 'helo', 'hlo', 'yo',
            'hi there', 'hello there', 'hi sir', 'hello sir', 'hi maam', 'hello maam',
            'hi mam', 'hello mam', 'hi dai', 'hi bro', 'hello bro',
            'good morning', 'good afternoon', 'good evening', 'good day',
            'namaste', 'namaskar', 'namaste sir', 'namaste dai', 'namaste hajur',
            'salam', 'hajur', 'k cha', 'ke cha', 'k xa', 'ke xa', 'kx', 'kexa',
        ],
    },
    {
        'key': 'closing_thanks',
        'label': 'Thanks',
        'priority': 'low',
        'match': 'exact',
        'alert': False,
        'locked': False,
        'enabled': True,
        'instruction': '',
        'keywords': [
            'thanks', 'thank you', 'thank u', 'thanku', 'thx', 'tnx', 'ty',
            'thanks a lot', 'thank you so much', 'thanks so much', 'many thanks',
            'ok thanks', 'okay thanks', 'ok thank you', 'thanks sir', 'thank you sir',
            'dhanyabad', 'dhanyawad', 'dhanyabad hajur',
            'ok', 'okay', 'okey', 'k', 'kk', 'sure', 'fine', 'got it', 'noted',
            'hunxa', 'huncha', 'hunchha', 'thik cha', 'thik xa', 'thikai cha', 'la',
            'bye', 'goodbye', 'good night', 'gn',
        ],
    },
    {
        'key': NOISE_KEY,
        'label': 'Spam',
        'priority': 'low',
        'match': 'exact',
        'alert': False,
        'locked': True,
        'enabled': True,
        'instruction': '',
        'keywords': [],
    },
    {
        'key': FALLBACK_KEY,
        'label': 'General Inquiry',
        'priority': 'low',
        'match': 'contains',
        'alert': False,
        'locked': True,
        'enabled': True,
        'instruction': '',
        'keywords': [],
    },
]

# What the AI is told to do about each built-in type when the rule carries no
# instruction of its own. Custom types get a generated line instead (see
# ai_router.build_dynamic_prompt).
BUILTIN_INSTRUCTIONS = {
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
    'order_status': (
        "\n--- ORDER STATUS MODE ACTIVE ---\n"
        "The customer is asking about an order they have already placed.\n"
        "1. NEVER invent a status, date, or tracking number — you cannot see their order.\n"
        "2. Ask for their order number or the phone number used to order.\n"
        "3. Reassure them it will be checked and answered shortly. Do NOT push a new sale."
    ),
    'delivery_query': (
        "\n--- DELIVERY INFO MODE ACTIVE ---\n"
        "Answer the delivery question using only the delivery details in the info above.\n"
        "If the charge or timing depends on location and you don't have it, ask where they\n"
        "are located instead of guessing. Keep it to 1-2 sentences."
    ),
    'greeting': (
        "\n--- GREETING MODE ACTIVE ---\n"
        "The customer only said hello. Greet them warmly in ONE short sentence and ask\n"
        "what they are looking for. Do NOT list products, prices, or links yet."
    ),
    'closing_thanks': (
        "\n--- CLOSING MODE ACTIVE ---\n"
        "The customer is thanking you or acknowledging. Reply with one short, warm line\n"
        "and invite them back. Do NOT restart the pitch or ask new questions."
    ),
    'spam_noise': (
        "\n--- MINIMAL RESPONSE MODE ---\n"
        "The message appears to be spam or just noise (emojis, random characters).\n"
        "Respond with a brief, friendly acknowledgment only. Do not engage deeply."
    ),
}

# One-line hints shown to the LLM classifier when it is enabled. Custom rules
# describe themselves from their own keywords.
BUILTIN_DESCRIPTIONS = {
    'purchase_intent': 'wants to buy, asks about price, payment, or how to order.',
    'product_issue': 'a complaint, refund request, or a damaged/wrong/missing product.',
    'order_status': 'asks where an order they already placed is, or for tracking.',
    'delivery_query': 'asks about delivery charge, delivery time, or coverage area.',
    'general_query': 'asks about product details, availability, or the business.',
    'greeting': 'only a greeting, with no question attached.',
    'closing_thanks': 'only thanks, an acknowledgement, or a sign-off.',
    'spam_noise': 'only emojis, random characters, or incomprehensible text.',
}


def default_rules():
    """A deep-ish copy of the built-ins, safe for a caller to mutate."""
    return [dict(rule, keywords=list(rule['keywords'])) for rule in DEFAULT_RULES]


# ─── Reading rules ────────────────────────────────────────────────────────────
def rules_for(chatbot_config):
    """
    The rule list a business classifies by.

    An empty `triage_rules` means "never customised", which resolves to the
    built-ins rather than to nothing — so a business that has never opened the
    panel keeps inheriting improvements to the defaults.
    """
    raw = getattr(chatbot_config, 'triage_rules', None) or []
    if not isinstance(raw, list) or not raw:
        return default_rules()
    try:
        return normalize_rules(raw)
    except ValueError:
        # Stored rules that no longer validate must not take the inbox down.
        return default_rules()


def rules_for_conversation(conversation):
    """The rules of the business whose page received this conversation."""
    integration = getattr(conversation, 'integration', None)
    config = getattr(integration, 'chatbot_config', None) if integration else None
    return rules_for(config)


def rule_by_key(rules, key):
    for rule in rules or ():
        if rule.get('key') == key:
            return rule
    return None


def display_map(rules):
    """`{key: (label, priority)}` — what the chips in a thread render from."""
    return {r['key']: (r['label'], r['priority']) for r in (rules or ())}


# ─── Classifying ──────────────────────────────────────────────────────────────
def normalized_phrase(text):
    """Message reduced to bare words — punctuation and emoji dropped, whitespace
    collapsed — so whole-message phrase sets can be matched exactly."""
    cleaned = ''.join(ch if (ch.isalnum() or ch.isspace()) else ' ' for ch in text)
    return ' '.join(cleaned.split())


def classify(message_text, rules=None):
    """
    Deterministic, free first pass. Returns ``(key, matched_keyword)``, or
    ``('', '')`` when nothing is conclusive and the message should go to the
    model.

    Rules are walked in list order and the first hit wins — which is why the
    panel lets an operator reorder them. Exact-match rules are safe anywhere in
    that order: they only fire when the keyword is the entire message.
    """
    rules = rules if rules is not None else DEFAULT_RULES
    text = (message_text or '').strip().lower()

    noise = rule_by_key(rules, NOISE_KEY)
    if not text:
        return (noise['key'], '') if noise else ('', '')

    phrase = normalized_phrase(text)

    for rule in rules:
        if not rule.get('enabled', True):
            continue
        keywords = rule.get('keywords') or []
        if not keywords:
            continue
        if rule.get('match') == 'exact':
            if phrase in keywords:
                return rule['key'], phrase
        else:
            for kw in keywords:
                if kw in text:
                    return rule['key'], kw

    # No letters or digits at all (emoji-only, punctuation-only) is noise.
    if not any(ch.isalnum() for ch in text) and noise:
        return noise['key'], ''
    return '', ''


# ─── Validating what the panel posts ──────────────────────────────────────────
_KEY_RE = re.compile(r'[^a-z0-9_]+')


def slugify_key(value):
    key = _KEY_RE.sub('_', str(value or '').strip().lower()).strip('_')
    return key[:MAX_KEY_LENGTH]


def normalize_rules(raw):
    """
    Clean and validate a posted rule list.

    Raises ``ValueError`` with a message meant for the operator — a rule set
    that silently drops half of what was typed is worse than a refused save,
    because the bot then answers by rules nobody chose.
    """
    if not isinstance(raw, list):
        raise ValueError("Triage rules must be a list.")
    if len(raw) > MAX_RULES:
        raise ValueError(f"Too many message types (max {MAX_RULES}).")

    cleaned = []
    seen_keys = set()

    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Rule #{index} is not valid.")

        label = str(item.get('label') or '').strip()
        if not label:
            raise ValueError(f"Rule #{index} needs a label.")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError(f'"{label[:20]}…" is too long (max {MAX_LABEL_LENGTH} characters).')

        key = slugify_key(item.get('key') or label)
        if not key:
            raise ValueError(f'"{label}" needs a name with at least one letter or number.')
        if key in seen_keys:
            raise ValueError(f'Two rules resolve to the same id "{key}" — rename one of them.')
        seen_keys.add(key)

        priority = str(item.get('priority') or 'low').strip().lower()
        if priority not in PRIORITIES:
            raise ValueError(f'"{label}" has an unknown priority "{priority}".')

        match = str(item.get('match') or 'contains').strip().lower()
        if match not in ('contains', 'exact'):
            raise ValueError(f'"{label}" has an unknown match mode "{match}".')

        raw_keywords = item.get('keywords') or []
        if isinstance(raw_keywords, str):
            raw_keywords = raw_keywords.split(',')
        if not isinstance(raw_keywords, list):
            raise ValueError(f'"{label}" has an unreadable keyword list.')
        if len(raw_keywords) > MAX_KEYWORDS_PER_RULE:
            raise ValueError(f'"{label}" has too many keywords (max {MAX_KEYWORDS_PER_RULE}).')

        keywords, seen_kw = [], set()
        for kw in raw_keywords:
            kw = ' '.join(str(kw).strip().lower().split())
            if not kw or kw in seen_kw:
                continue
            # A short substring matches inside unrelated words — 'hi' inside
            # "this", 'ok' inside "broken" — and quietly swallows every message.
            # Whole-message matching is the safe home for those.
            if match == 'contains' and len(kw) < MIN_CONTAINS_KEYWORD_LENGTH:
                raise ValueError(
                    f'"{kw}" is too short to match anywhere in a message (it would match '
                    f'inside other words). Put it in a rule set to "whole message" instead.'
                )
            seen_kw.add(kw)
            keywords.append(kw)

        cleaned.append({
            'key': key,
            'label': label,
            'priority': priority,
            'match': match,
            'keywords': keywords,
            'alert': bool(item.get('alert')),
            'instruction': str(item.get('instruction') or '').strip(),
            'enabled': bool(item.get('enabled', True)),
            'locked': key in LOCKED_KEYS,
        })

    missing = [k for k in LOCKED_KEYS if k not in seen_keys]
    if missing:
        names = ', '.join(
            rule_by_key(DEFAULT_RULES, k)['label'] for k in missing
        )
        raise ValueError(f"{names} can't be removed — it's where unmatched messages land.")

    # The fallback can never win by keyword, so its position is irrelevant; it
    # is kept last so the panel reads the way the classifier runs.
    cleaned.sort(key=lambda r: r['key'] == FALLBACK_KEY)
    return cleaned
