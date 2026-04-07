"""
One-time migration: copy config.py keys → DB for admin user.

Also copies existing price file and cache to admin's per-user directory.

Run: python migrate_config.py
"""

import shutil
from pathlib import Path
from db import init_db, get_user, save_credential
from crypto import encrypt

init_db()

# Get admin user
admin = get_user("admin")
if not admin:
    print("Admin user not found. Run the app first.")
    exit(1)

uid = admin["id"]
print(f"Admin user: id={uid}")

# Import credentials from config.py
try:
    from config import (
        OZON_SELLER_CLIENT_ID, OZON_SELLER_API_KEY,
        OZON_SELLER_API_KEY_V2,
        OZON_PERF_CLIENT_ID, OZON_PERF_CLIENT_SECRET,
        WB_API_KEY,
        YM_API_KEY, YM_CAMPAIGN_ID, YM_BUSINESS_ID,
        DEEPSEEK_API_KEY, GIGACHAT_CREDENTIALS,
        PRICE_FILE,
    )
except ImportError as e:
    print(f"Cannot import config: {e}")
    exit(1)

# Ozon
ozon_keys = {
    "OZON_SELLER_CLIENT_ID": OZON_SELLER_CLIENT_ID,
    "OZON_SELLER_API_KEY": OZON_SELLER_API_KEY,
    "OZON_SELLER_API_KEY_V2": OZON_SELLER_API_KEY_V2,
    "OZON_PERF_CLIENT_ID": OZON_PERF_CLIENT_ID,
    "OZON_PERF_CLIENT_SECRET": OZON_PERF_CLIENT_SECRET,
}
for k, v in ozon_keys.items():
    if v:
        save_credential(uid, "ozon", k, encrypt(str(v)))
        print(f"  ozon.{k}: saved")

# WB
if WB_API_KEY:
    save_credential(uid, "wb", "WB_API_KEY", encrypt(WB_API_KEY))
    print("  wb.WB_API_KEY: saved")

# YM
ym_keys = {
    "YM_API_KEY": YM_API_KEY,
    "YM_CAMPAIGN_ID": str(YM_CAMPAIGN_ID),
    "YM_BUSINESS_ID": str(YM_BUSINESS_ID),
}
for k, v in ym_keys.items():
    if v:
        save_credential(uid, "ym", k, encrypt(str(v)))
        print(f"  ym.{k}: saved")

# AI
ai_keys = {
    "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
    "GIGACHAT_CREDENTIALS": GIGACHAT_CREDENTIALS,
}
for k, v in ai_keys.items():
    if v:
        save_credential(uid, "ai", k, encrypt(str(v)))
        print(f"  ai.{k}: saved")

# Copy price file
user_dir = Path(f"data/user_{uid}")
user_dir.mkdir(parents=True, exist_ok=True)

price_src = Path(PRICE_FILE)
if price_src.exists():
    dst = user_dir / "price_current.xlsx"
    shutil.copy2(price_src, dst)
    print(f"\nPrice file copied: {price_src} → {dst}")

    from db import save_price_list
    save_price_list(uid, price_src.name, str(dst), 0)
    print("  Price list recorded in DB")

# Copy cache
cache_src = Path("cache")
cache_dst = user_dir / "cache"
if cache_src.exists():
    cache_dst.mkdir(exist_ok=True)
    copied = 0
    for f in cache_src.glob("*.pkl"):
        shutil.copy2(f, cache_dst / f.name)
        copied += 1
    print(f"\nCache copied: {copied} files → {cache_dst}")

print("\nMigration complete!")
