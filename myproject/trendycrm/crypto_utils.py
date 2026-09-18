import base64
from cryptography.fernet import Fernet
from django.conf import settings
import hashlib
import logging

logger = logging.getLogger(__name__)

def _get_fernet() -> Fernet:
    """
    Derives a 32-byte URL-safe base64-encoded key from Django's SECRET_KEY.
    """
    # SECRET_KEY can be anything, but Fernet needs exactly 32 bytes base64 url-safe.
    # We use SHA-256 to hash the SECRET_KEY to 32 bytes, then b64encode it.
    key_bytes = settings.SECRET_KEY.encode('utf-8')
    hashed_key = hashlib.sha256(key_bytes).digest()
    fernet_key = base64.urlsafe_b64encode(hashed_key)
    return Fernet(fernet_key)

def encrypt_api_key(api_key: str) -> str:
    """Encrypts a plaintext API key for secure storage."""
    if not api_key:
        return ""
    try:
        f = _get_fernet()
        return f.encrypt(api_key.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Error encrypting API key: {e}")
        return ""

def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypts a previously encrypted API key."""
    if not encrypted_key:
        return ""
    
    # If the key doesn't look like a Fernet token, assume it's legacy plaintext
    # Fernet tokens usually start with 'gAAAAA'
    if not encrypted_key.startswith('gAAAAA'):
        return encrypted_key
        
    try:
        f = _get_fernet()
        return f.decrypt(encrypted_key.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Error decrypting API key: {e}")
        return ""
