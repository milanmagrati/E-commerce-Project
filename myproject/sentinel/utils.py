"""
Forensic helpers: turn a raw HTTP request into the who/where/what-hardware
facts Sentinel Vault stores.

Deliberately dependency-free — the project pins its requirements tightly and a
user-agent database is not worth a new package for this. The parser below is
ordered most-specific-first, which is the only thing that matters for UA strings
(every browser lies about being every other browser).
"""

import hashlib
import ipaddress
import re

from .models import DeviceKind

# ─────────────────────────────────────────────────────────────────────────────
# User-agent parsing
# ─────────────────────────────────────────────────────────────────────────────

# Order matters: Edge claims Chrome, Chrome claims Safari, Opera claims both.
_BROWSERS = [
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

_OPERATING_SYSTEMS = [
    ('Windows 11', r'Windows NT 10\.0.*(?:Win64|WOW64)', ''),   # 11 is indistinguishable from 10 in the UA
    ('Windows 10', r'Windows NT 10\.0', ''),
    ('Windows 8.1', r'Windows NT 6\.3', ''),
    ('Windows 8', r'Windows NT 6\.2', ''),
    ('Windows 7', r'Windows NT 6\.1', ''),
    ('Android', r'Android ([\d.]+)', r'Android ([\d.]+)'),
    ('iOS', r'(?:iPhone|iPad|iPod).*OS ([\d_]+)', r'OS ([\d_]+)'),
    ('macOS', r'Mac OS X ([\d_.]+)', r'Mac OS X ([\d_.]+)'),
    ('Chrome OS', r'CrOS', ''),
    ('Ubuntu', r'Ubuntu', ''),
    ('Linux', r'Linux', ''),
]

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


def parse_user_agent(ua):
    """Return a dict of device facts. Never raises — a garbage UA yields Unknowns."""
    ua = (ua or '').strip()
    facts = {
        'browser': '', 'browser_version': '',
        'operating_system': '', 'os_version': '',
        'device_kind': DeviceKind.UNKNOWN, 'device_brand': '',
    }
    if not ua:
        return facts

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

    return facts


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

def get_client_ip(request):
    """Best-effort real client IP.

    Trusts X-Forwarded-For's left-most entry, which is correct behind the
    cPanel/Apache proxy this project deploys to. Returns (ip, raw_chain).
    """
    chain = request.META.get('HTTP_X_FORWARDED_FOR', '') or ''
    candidates = [c.strip() for c in chain.split(',') if c.strip()]
    candidates.append(request.META.get('HTTP_X_REAL_IP', '') or '')
    candidates.append(request.META.get('REMOTE_ADDR', '') or '')

    for candidate in candidates:
        if not candidate:
            continue
        # Strip a :port suffix on bare IPv4 and normalise IPv6 brackets.
        candidate = candidate.strip('[]')
        if candidate.count(':') == 1 and '.' in candidate:
            candidate = candidate.split(':')[0]
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
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
