"""
Forensic helpers: turn a raw HTTP request into the who/where/what-hardware
facts Sentinel Vault stores.

Deliberately dependency-free — the project pins its requirements tightly and a
user-agent database is not worth a new package for this. The parser below is
ordered most-specific-first, which is the only thing that matters for UA strings
(every browser lies about being every other browser).

The UA string alone is not enough, though: Chrome for Android's "Request desktop
site" sends a verbatim Linux-desktop UA, which is why a phone used to land in the
registry as "Chrome on Linux (Desktop)". Client hints (see `read_client_hints`)
carry the facts the UA hides — touch points and real screen size are not spoofed
by desktop mode — so the parser takes them as a second opinion.
"""

import hashlib
import ipaddress
import json
import re
from urllib.parse import unquote

from .models import DeviceKind

# ─────────────────────────────────────────────────────────────────────────────
# User-agent parsing
# ─────────────────────────────────────────────────────────────────────────────

# Order matters: Edge claims Chrome, Chrome claims Safari, Opera claims both.
# In-app browsers come first of all — a Facebook or Instagram webview also says
# "Chrome", and for this business "opened from the Instagram app" is the more
# useful fact.
_BROWSERS = [
    ('Facebook App', r'(?:FBAV|FBAN|FB_IAB)[/ ]?([\d.]*)'),
    ('Instagram', r'Instagram[ /]([\d.]*)'),
    ('TikTok', r'(?:musical_ly|BytedanceWebview|Trill)[/ ]?([\d.]*)'),
    ('LINE', r'Line/([\d.]+)'),
    ('Edge', r'Edg(?:e|A|iOS)?/([\d.]+)'),
    ('Opera', r'(?:OPR|Opera)/([\d.]+)'),
    ('Vivaldi', r'Vivaldi/([\d.]+)'),
    ('Brave', r'Brave/([\d.]+)'),
    ('Samsung Internet', r'SamsungBrowser/([\d.]+)'),
    ('UC Browser', r'UCBrowser/([\d.]+)'),
    ('Firefox', r'(?:Firefox|FxiOS)/([\d.]+)'),
    ('Chrome', r'(?:Chrome|CriOS)/([\d.]+)'),
    ('Safari', r'Version/([\d.]+).*Safari'),
    ('Internet Explorer', r'(?:MSIE |rv:)([\d.]+).*Trident'),
]

# (name, detection pattern, version pattern). Detection must not require a
# version: Firefox for Android and several webviews send a bare "Android" with
# no number, and demanding one used to drop them all the way through to "Linux".
_OPERATING_SYSTEMS = [
    ('Windows 11', r'Windows NT 10\.0.*(?:Win64|WOW64)', ''),   # 11 is indistinguishable from 10 in the UA
    ('Windows 10', r'Windows NT 10\.0', ''),
    ('Windows 8.1', r'Windows NT 6\.3', ''),
    ('Windows 8', r'Windows NT 6\.2', ''),
    ('Windows 7', r'Windows NT 6\.1', ''),
    ('Android', r'Android', r'Android[\s/]([\d.]+)'),
    ('iOS', r'(?:iPhone|iPad|iPod)', r'OS ([\d_]+)'),
    ('macOS', r'Mac OS X ([\d_.]+)', r'Mac OS X ([\d_.]+)'),
    ('Chrome OS', r'CrOS', ''),
    ('Ubuntu', r'Ubuntu', ''),
    ('Linux', r'Linux', ''),
]

# Operating systems that can only be a real computer, and ones that can only be
# a handheld. Anything outside both lists (Linux, Unknown) is where a spoofed
# desktop-mode UA lands, and is therefore where the client hints get a vote.
_DESKTOP_ONLY_OS = {'Windows 11', 'Windows 10', 'Windows 8.1', 'Windows 8', 'Windows 7',
                    'macOS', 'Chrome OS'}
_HANDHELD_OS = {'Android', 'iOS', 'iPadOS'}

_BOT_PATTERN = re.compile(
    r'bot|crawler|spider|crawling|slurp|facebookexternalhit|whatsapp|telegram|'
    r'preview|monitor|uptime|pingdom|curl|wget|python-requests|okhttp|postman|axios',
    re.I,
)

_TABLET_PATTERN = re.compile(r'iPad|Tablet|PlayBook|Silk|Android(?!.*Mobile)', re.I)
_MOBILE_PATTERN = re.compile(r'Mobile|iPhone|iPod|Android|BlackBerry|IEMobile|Opera Mini', re.I)

_DEVICE_BRANDS = [
    ('Apple', r'iPhone|iPad|iPod|Macintosh'),
    ('Samsung', r'SM-|Samsung|GT-'),
    ('Xiaomi', r'Redmi|Mi \d|POCO|Xiaomi'),
    ('OnePlus', r'ONEPLUS'),
    ('Oppo', r'OPPO|CPH\d'),
    ('Vivo', r'vivo|V\d{4}'),
    ('Realme', r'RMX\d|realme'),
    ('Huawei', r'HUAWEI|Honor'),
    ('Google', r'Pixel'),
]


def parse_user_agent(ua, hints=None):
    """Return a dict of device facts. Never raises — a garbage UA yields Unknowns.

    `hints` is the dict from `read_client_hints()`. It is optional so the parser
    stays usable (and testable) on a bare UA string.
    """
    ua = (ua or '').strip()
    facts = {
        'browser': '', 'browser_version': '',
        'operating_system': '', 'os_version': '',
        'device_kind': DeviceKind.UNKNOWN, 'device_brand': '',
    }
    if not ua:
        return _apply_hints(facts, hints) if hints else facts

    if _BOT_PATTERN.search(ua):
        facts['device_kind'] = DeviceKind.BOT
        # Name the agent anyway — "curl" is far more useful than "Bot".
        head = re.split(r'[/\s]', ua, 1)[0][:60]
        facts['browser'] = head or 'Bot'
        facts['operating_system'] = 'Automated'
        return facts

    for name, pattern in _BROWSERS:
        match = re.search(pattern, ua)
        if match:
            facts['browser'] = name
            facts['browser_version'] = (match.group(1) or '').split('.')[0]
            break
    else:
        facts['browser'] = 'Unknown'

    for name, pattern, version_pattern in _OPERATING_SYSTEMS:
        if re.search(pattern, ua):
            facts['operating_system'] = name
            if version_pattern:
                vmatch = re.search(version_pattern, ua)
                if vmatch:
                    facts['os_version'] = vmatch.group(1).replace('_', '.')
            break
    else:
        facts['operating_system'] = 'Unknown'

    if _TABLET_PATTERN.search(ua):
        facts['device_kind'] = DeviceKind.TABLET
    elif _MOBILE_PATTERN.search(ua):
        facts['device_kind'] = DeviceKind.MOBILE
    else:
        facts['device_kind'] = DeviceKind.DESKTOP

    for brand, pattern in _DEVICE_BRANDS:
        if re.search(pattern, ua, re.I):
            facts['device_brand'] = brand
            break

    return _apply_hints(facts, hints) if hints else facts


# ─────────────────────────────────────────────────────────────────────────────
# Client hints
# ─────────────────────────────────────────────────────────────────────────────

HINT_COOKIE = 'sv_dev'

# Chrome sends these low-entropy hints on every request without being asked; the
# rest arrive once the response has advertised them via Accept-CH (middleware).
_UA_CH_HEADERS = {
    'platform': 'HTTP_SEC_CH_UA_PLATFORM',
    'platform_version': 'HTTP_SEC_CH_UA_PLATFORM_VERSION',
    'model': 'HTTP_SEC_CH_UA_MODEL',
}

_MAX_HINT_COOKIE = 400


def _hint_int(value, limit=100000):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= limit else None


def read_client_hints(request):
    """Collect every non-UA signal this request carries about the machine.

    Three sources, weakest first so the strongest wins the dict:
      1. `Sec-CH-UA-*` request headers — free, but Chrome's desktop-site mode
         rewrites them to match the spoofed UA, so they cannot stand alone.
      2. The `sv_dev` cookie our own snippet writes from JavaScript. `screen.*`
         and `navigator.maxTouchPoints` are *not* rewritten by desktop mode,
         which makes this the signal that actually identifies a phone.
      3. POST fields on the login form, kept for the original hidden-input form
         of this data.

    Never raises: a truncated or hand-edited cookie just yields fewer facts.
    """
    hints = {}
    if request is None:
        return hints

    meta = getattr(request, 'META', None) or {}

    for key, header in _UA_CH_HEADERS.items():
        raw = (meta.get(header) or '').strip().strip('"')[:60]
        if raw:
            hints[key] = raw
    mobile_header = (meta.get('HTTP_SEC_CH_UA_MOBILE') or '').strip()
    if mobile_header in ('?0', '?1'):
        hints['mobile'] = mobile_header == '?1'

    raw_cookie = (getattr(request, 'COOKIES', None) or {}).get(HINT_COOKIE, '')
    if raw_cookie and len(raw_cookie) <= _MAX_HINT_COOKIE:
        try:
            payload = json.loads(unquote(raw_cookie))
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            width, height = _hint_int(payload.get('w')), _hint_int(payload.get('h'))
            if width and height:
                hints['screen'] = f'{width}x{height}'
                # Desktop-site mode reports the panel in physical pixels with a
                # ratio of 1, normal mode reports CSS pixels with the real ratio.
                # Multiplying the two cancels the difference, so the short edge
                # means the same thing either way.
                try:
                    ratio = float(payload.get('d') or 1) or 1
                except (TypeError, ValueError):
                    ratio = 1
                ratio = min(max(ratio, 0.5), 6)
                hints['short_edge_px'] = int(min(width, height) * ratio)
            touch = _hint_int(payload.get('t'), limit=64)
            if touch is not None:
                hints['touch'] = touch
            for source, target in (('tz', 'timezone'), ('l', 'language'),
                                   ('p', 'platform'), ('m', 'model')):
                value = payload.get(source)
                if isinstance(value, str) and value.strip():
                    hints[target] = value.strip()[:64]
            if isinstance(payload.get('mb'), bool):
                hints['mobile'] = payload['mb']

    # Inside the try on purpose: touching .POST is what parses the body, and on
    # a streamed upload or an already-read request that raises. This runs after
    # the view on some paths, so it must never be the thing that breaks one.
    try:
        post = request.POST
        if post.get('client_timezone'):
            hints['timezone'] = post['client_timezone'][:64]
        if post.get('client_screen'):
            hints['screen'] = post['client_screen'][:24]
    except Exception:
        pass

    if not hints.get('language'):
        language = (meta.get('HTTP_ACCEPT_LANGUAGE') or '').split(',')[0].strip()
        if language:
            hints['language'] = language[:32]
    return hints


def _apply_hints(facts, hints):
    """Let the client hints correct what the UA string got wrong.

    Upgrade-only by design. A mobile UA is never a lie in the direction that
    matters (nothing spoofs *down* to a phone), so hints may promote a
    "desktop" verdict to mobile/tablet but never the reverse — otherwise a
    Windows touchscreen laptop or a stale cookie could start reporting real
    computers as phones.
    """
    if not hints:
        return facts

    platform = (hints.get('platform') or '').strip()
    os_name = facts.get('operating_system') or ''

    # A platform hint of Android/iOS is decisive: no desktop reports those.
    hinted_os = {'android': 'Android', 'ios': 'iOS', 'ipados': 'iPadOS'}.get(platform.lower())
    if hinted_os and os_name not in _HANDHELD_OS:
        facts['operating_system'] = hinted_os
        version = (hints.get('platform_version') or '').strip()
        if version:
            facts['os_version'] = version[:30]
        os_name = hinted_os

    touch = bool(hints.get('touch'))

    # Safari on an iPad has claimed to be a Mac since iPadOS 13. A Mac has no
    # touchscreen, so touch points are the one thing that tells them apart.
    if os_name == 'macOS' and touch:
        facts['operating_system'] = os_name = 'iPadOS'
        facts['device_kind'] = DeviceKind.TABLET
        return facts

    handheld = os_name in _HANDHELD_OS
    if not handheld and os_name not in _DESKTOP_ONLY_OS:
        # Linux/Unknown — the bucket Chrome's "Request desktop site" drops a
        # phone into. Physical touch points settle it: desktop Linux machines
        # report zero, and desktop mode does not fake them.
        handheld = touch and hints.get('mobile') is not False
        if handheld:
            # A touchscreen handheld sending a Linux desktop UA is an Android
            # device — desktop mode on iOS spoofs macOS instead, and that case
            # is caught above. Leaving it as "Linux" would name the kernel and
            # hide the phone, which is the opposite of the point.
            facts['operating_system'] = os_name = 'Android'
            facts['os_version'] = ''

    if handheld and facts.get('device_kind') in (DeviceKind.DESKTOP, DeviceKind.UNKNOWN):
        facts['device_kind'] = _handheld_kind(hints)

    model = (hints.get('model') or '').strip()
    if model and not facts.get('device_brand'):
        for brand, pattern in _DEVICE_BRANDS:
            if re.search(pattern, model, re.I):
                facts['device_brand'] = brand
                break
    return facts


def _handheld_kind(hints):
    """Phone or tablet, from whatever measurements we were given."""
    model = (hints.get('model') or '')
    if re.search(r'iPad|Tab\b|Tablet', model, re.I):
        return DeviceKind.TABLET
    if hints.get('mobile') is True:
        return DeviceKind.MOBILE
    shortest = hints.get('short_edge_px')
    if shortest:
        # Phone panels stop at ~1440 physical pixels on the short edge; every
        # tablet starts above 1480. Ambiguity here costs a wrong icon, not a
        # wrong verdict, so the split is fine where the gap is.
        return DeviceKind.TABLET if shortest >= 1450 else DeviceKind.MOBILE
    return DeviceKind.MOBILE


def device_label(facts):
    """Short human label for a device, e.g. 'Chrome on Windows 11 (Desktop)'."""
    browser = facts.get('browser') or 'Unknown browser'
    os_name = facts.get('operating_system') or 'Unknown OS'
    kind = facts.get('device_kind') or DeviceKind.UNKNOWN
    kind_display = dict(DeviceKind.choices).get(kind, 'Unknown')
    return f'{browser} on {os_name} ({kind_display})'


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

def _clean_ip(raw):
    """Validated address from a header fragment, or None."""
    candidate = (raw or '').strip()
    if not candidate:
        return None
    # '[::1]:8000' → '::1'; '10.0.0.1:53' → '10.0.0.1'. A bare IPv6 address has
    # several colons and no brackets, so only split when there is exactly one.
    if candidate.startswith('['):
        candidate = candidate[1:].split(']')[0]
    elif candidate.count(':') == 1 and '.' in candidate:
        candidate = candidate.split(':')[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def _is_infrastructure(ip):
    """True for addresses that can only be one of our own hops, not a visitor."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved


def get_client_ip(request):
    """Best-effort real client IP. Returns (ip, raw_chain).

    Read right-to-left, not left-to-right. Each proxy *appends* to
    X-Forwarded-For, so the right-most entry is the one our own edge wrote and
    everything further left is whatever the caller chose to send. Taking the
    left-most entry let anyone attribute their actions to any IP they liked by
    setting the header themselves — in an audit log that is the difference
    between evidence and decoration.

    So: walk inwards from the right, past our own private hops, and stop at the
    first address that could be a real visitor.
    """
    meta = getattr(request, 'META', None) or {}
    chain = meta.get('HTTP_X_FORWARDED_FOR', '') or ''
    forwarded = [ip for ip in (_clean_ip(part) for part in chain.split(',')) if ip]

    for candidate in reversed(forwarded):
        if not _is_infrastructure(candidate):
            return candidate, chain[:255]

    # Every hop was private: staff on the office LAN, or a loopback proxy in
    # development. The left-most entry is then the closest thing to the client.
    if forwarded:
        return forwarded[0], chain[:255]

    for header in ('HTTP_X_REAL_IP', 'REMOTE_ADDR'):
        candidate = _clean_ip(meta.get(header, ''))
        if candidate:
            return candidate, chain[:255]
    return None, chain[:255]


def network_label(ip):
    """Classify an IP without any external lookup — no geo API calls, no latency."""
    if not ip:
        return 'Unknown'
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return 'Invalid'
    if addr.is_loopback:
        return 'Loopback'
    if addr.is_private:
        return 'Private LAN'
    if addr.is_reserved or addr.is_link_local:
        return 'Reserved'
    return 'Public Internet'


# ─────────────────────────────────────────────────────────────────────────────
# Fingerprinting
# ─────────────────────────────────────────────────────────────────────────────

def device_fingerprint(user_agent, ip, facts=None):
    """Stable-ish id for "same machine".

    Built from the durable half of the UA (browser family + OS, not the version,
    which changes on every auto-update) plus the /24 network. Deliberately coarse:
    a false "new device" alert on every Chrome update would train admins to ignore
    the alerts entirely.
    """
    facts = facts or parse_user_agent(user_agent)
    network = ''
    if ip:
        try:
            addr = ipaddress.ip_address(ip)
            if addr.version == 4:
                network = '.'.join(ip.split('.')[:3])
            else:
                network = ':'.join(ip.split(':')[:4])
        except ValueError:
            network = ''

    seed = '|'.join([
        facts.get('browser', ''),
        facts.get('operating_system', ''),
        str(facts.get('device_kind', '')),
        facts.get('device_brand', ''),
        network,
    ])
    return hashlib.sha256(seed.encode('utf-8', 'ignore')).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Value redaction / serialisation
# ─────────────────────────────────────────────────────────────────────────────

_SENSITIVE = re.compile(
    r'password|passwd|secret|token|api_key|apikey|private_key|salt|signature|'
    r'credential|otp|cvv|card_number|access_key',
    re.I,
)

REDACTED = '••••••••'


def is_sensitive(field_name):
    return bool(_SENSITIVE.search(field_name or ''))


def redact_value(value):
    """Mask a secret while keeping *changes* to it detectable.

    Returning a bare constant would make every password look identical, so a
    password reset would produce no diff and vanish from the trail. Appending a
    short digest of the value means two different secrets compare unequal —
    the audit shows that the credential changed — while the value itself stays
    unrecoverable (it is already a PBKDF2 hash at this point, and 8 hex chars
    of a SHA-256 over it is not reversible).
    """
    if value in (None, ''):
        return REDACTED
    digest = hashlib.sha256(str(value).encode('utf-8', 'ignore')).hexdigest()[:8]
    return f'{REDACTED}·{digest}'


def serialise_value(value, limit=300):
    """JSON-safe, length-capped representation of any model field value."""
    from datetime import date, datetime, time
    from decimal import Decimal
    from uuid import UUID

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [serialise_value(v, 80) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): serialise_value(v, 80) for k, v in list(value.items())[:20]}

    text = str(value)
    return text if len(text) <= limit else text[:limit] + '…'


def scrub_post_data(post_data, limit=25):
    """Redacted, truncated snapshot of submitted form data for the event context."""
    scrubbed = {}
    for key in list(post_data.keys())[:limit]:
        if key in ('csrfmiddlewaretoken',):
            continue
        if is_sensitive(key):
            scrubbed[key] = REDACTED
            continue
        values = post_data.getlist(key) if hasattr(post_data, 'getlist') else [post_data[key]]
        scrubbed[key] = serialise_value(values[0] if len(values) == 1 else values, 200)
    return scrubbed
