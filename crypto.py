"""
Fernet encryption for API keys at rest.

Key source (priority):
  1. Environment variable ALCHEMY_FERNET_KEY
  2. File data/.fernet.key (auto-generated on first run)
"""

import os
from pathlib import Path
from cryptography.fernet import Fernet

_KEY_FILE = Path("data/.fernet.key")
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("ALCHEMY_FERNET_KEY")
    if key:
        _fernet = Fernet(key.encode())
        return _fernet

    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text().strip()
    else:
        key = Fernet.generate_key().decode()
        _KEY_FILE.write_text(key)
        print(f"🔑 Fernet-ключ создан: {_KEY_FILE}")

    _fernet = Fernet(key.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt string → base64 token."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt base64 token → string."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
