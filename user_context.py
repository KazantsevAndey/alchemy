"""
Per-user context: credentials, paths, price loading.

Bridge between DB and API modules.
"""

from pathlib import Path
from db import get_credentials, get_active_price
from crypto import decrypt

DATA_DIR = Path("data")


def get_user_data_dir(user_id: int) -> Path:
    p = DATA_DIR / f"user_{user_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_cache_dir(user_id: int) -> Path:
    p = get_user_data_dir(user_id) / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_price_path(user_id: int) -> Path | None:
    """Return path to active price xlsx, or None."""
    rec = get_active_price(user_id)
    if rec:
        p = Path(rec["file_path"])
        if p.exists():
            return p
    return None


def get_user_credentials(user_id: int) -> dict:
    """Return flat dict of decrypted credentials for all marketplaces.

    Keys match config.py names:
        OZON_SELLER_CLIENT_ID, OZON_SELLER_API_KEY, OZON_SELLER_API_KEY_V2,
        OZON_PERF_CLIENT_ID, OZON_PERF_CLIENT_SECRET,
        WB_API_KEY,
        YM_API_KEY, YM_CAMPAIGN_ID, YM_BUSINESS_ID,
        DEEPSEEK_API_KEY, GIGACHAT_CREDENTIALS
    """
    result = {}
    for mp in ("ozon", "wb", "ym", "ai"):
        encrypted = get_credentials(user_id, mp)
        for key_name, enc_val in encrypted.items():
            try:
                result[key_name] = decrypt(enc_val)
            except Exception:
                pass  # corrupted key — skip
    return result


def has_marketplace_credentials(user_id: int, marketplace: str) -> bool:
    """Check if user has any credentials for a marketplace."""
    required = {
        "ozon": ["OZON_SELLER_CLIENT_ID", "OZON_SELLER_API_KEY"],
        "wb": ["WB_API_KEY"],
        "ym": ["YM_API_KEY", "YM_CAMPAIGN_ID", "YM_BUSINESS_ID"],
    }
    creds = get_user_credentials(user_id)
    for key in required.get(marketplace, []):
        if not creds.get(key):
            return False
    return True
