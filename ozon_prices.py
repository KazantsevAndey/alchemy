"""
Цены Ozon — Seller API.

Endpoints:
  - /v5/product/info/prices — цены, комиссии (до 1000 за запрос, пагинация cursor)
  - /v3/product/info/list   — названия товаров (до 1000 offer_id за запрос)

Ключ: OZON_SELLER_API_KEY_V2 (расширенный, scope: product).

Ozon Карта:
  Seller API не отдаёт цену по Ozon Карте — это внутренняя скидка Ozon
  для держателей карты, продавец не управляет ею через API.
"""

import pandas as pd
import requests
import time

from config import OZON_SELLER_CLIENT_ID, OZON_SELLER_API_KEY_V2

HEADERS = {
    "Client-Id": OZON_SELLER_CLIENT_ID,
    "Api-Key": OZON_SELLER_API_KEY_V2,
    "Content-Type": "application/json",
}

BASE = "https://api-seller.ozon.ru"


def _fetch_names(offer_ids: list[str]) -> dict[str, str]:
    """Получить названия товаров по offer_id через /v3/product/info/list."""
    names = {}
    for i in range(0, len(offer_ids), 1000):
        batch = offer_ids[i:i + 1000]
        resp = requests.post(
            f"{BASE}/v3/product/info/list",
            headers=HEADERS,
            json={"offer_id": batch},
        )
        if resp.status_code != 200:
            print(f"  Ошибка names: {resp.status_code}")
            continue
        for it in resp.json().get("items", []):
            names[it.get("offer_id", "")] = it.get("name", "")
        time.sleep(0.3)
    return names


def get_ozon_prices(offer_ids: list[str] | None = None) -> pd.DataFrame:
    """Загружает цены товаров из /v5/product/info/prices.

    Args:
        offer_ids: если указан — грузим только эти артикулы.

    Returns:
        DataFrame: offer_id, name, price, old_price,
                   marketing_seller_price, min_price
    """
    t0 = time.time()
    all_items = []

    if offer_ids:
        # Фильтруем невалидные
        offer_ids = [a for a in offer_ids if a and a != 'nan']
        batches = [offer_ids[i:i + 1000] for i in range(0, len(offer_ids), 1000)]
    else:
        batches = [None]

    page = 0
    for batch in batches:
        cursor = ""
        while True:
            body = {"limit": 1000}
            if batch:
                body["filter"] = {"offer_id": batch, "visibility": "ALL"}
            else:
                body["filter"] = {"visibility": "ALL"}
            if cursor:
                body["cursor"] = cursor

            resp = requests.post(f"{BASE}/v5/product/info/prices", headers=HEADERS, json=body)
            if resp.status_code != 200:
                print(f"  Ошибка цен Ozon: {resp.status_code} — {resp.text[:200]}")
                break

            # v5 отдаёт items на верхнем уровне (без result)
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break

            for it in items:
                p = it.get("price", {})
                all_items.append({
                    "offer_id": it.get("offer_id", ""),
                    "product_id": it.get("product_id", 0),
                    "price": float(p.get("price", 0) or 0),
                    "old_price": float(p.get("old_price", 0) or 0),
                    "marketing_seller_price": float(p.get("marketing_seller_price", 0) or 0),
                    "min_price": float(p.get("min_price", 0) or 0),
                })

            page += 1
            cursor = data.get("cursor", "")
            has_next = data.get("has_next", False)
            print(f"  Цены стр.{page}: {len(items)} товаров")

            if not has_next or not cursor:
                break
            time.sleep(0.3)

    if not all_items:
        print("  Ozon цены: 0 товаров")
        return pd.DataFrame()

    df = pd.DataFrame(all_items)
    df["offer_id"] = df["offer_id"].astype(str).str.strip()
    df = df[df["offer_id"] != ""].reset_index(drop=True)

    # Получаем названия
    print(f"  Загрузка названий для {len(df)} товаров...")
    names = _fetch_names(df["offer_id"].tolist())
    df["name"] = df["offer_id"].map(names).fillna("")

    elapsed = time.time() - t0
    print(f"  Ozon цены: {len(df)} товаров за {elapsed:.1f}с")
    return df
