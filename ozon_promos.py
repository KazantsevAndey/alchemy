"""
Акции Ozon — Seller API.

Endpoints:
  - GET  /v1/actions              — список активных акций
  - POST /v1/actions/candidates   — товары-кандидаты для акции
  - POST /v1/actions/products     — товары, уже участвующие в акции

Ключ: OZON_SELLER_API_KEY_V2 (расширенный, scope: product).
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


def get_actions() -> pd.DataFrame:
    """Список активных акций Ozon.

    Returns:
        DataFrame: id, title, date_start, date_end,
                   participating_products_count, potential_products_count и др.
    """
    resp = requests.get(f"{BASE}/v1/actions", headers=HEADERS)
    if resp.status_code != 200:
        print(f"  Ошибка акций Ozon: {resp.status_code} — {resp.text[:200]}")
        return pd.DataFrame()

    data = resp.json()
    items = data.get("result", [])
    if not items:
        print("  Ozon: акций не найдено")
        return pd.DataFrame()

    df = pd.DataFrame(items)
    print(f"  Ozon акции: {len(df)}")
    return df


def get_action_candidates(action_id: int, limit: int = 100) -> pd.DataFrame:
    """Товары-кандидаты для участия в акции.

    Returns:
        DataFrame: id (product_id), price, action_price, max_action_price,
                   min_price, add_mode и др.
    """
    all_items = []
    offset = 0

    while True:
        body = {"action_id": action_id, "limit": limit, "offset": offset}
        resp = requests.post(f"{BASE}/v1/actions/candidates", headers=HEADERS, json=body)
        if resp.status_code != 200:
            print(f"  Ошибка кандидатов акции {action_id}: {resp.status_code}")
            break

        data = resp.json()
        products = data.get("result", {}).get("products", [])
        if not products:
            break

        all_items.extend(products)
        if len(products) < limit:
            break
        offset += limit
        time.sleep(0.3)

    if not all_items:
        return pd.DataFrame()

    df = pd.json_normalize(all_items)
    return df


def get_action_products(action_id: int, limit: int = 100) -> pd.DataFrame:
    """Товары, уже участвующие в акции.

    Returns:
        DataFrame: id (product_id), price, action_price, max_action_price и др.
    """
    all_items = []
    offset = 0

    while True:
        body = {"action_id": action_id, "limit": limit, "offset": offset}
        resp = requests.post(f"{BASE}/v1/actions/products", headers=HEADERS, json=body)
        if resp.status_code != 200:
            print(f"  Ошибка товаров акции {action_id}: {resp.status_code}")
            break

        data = resp.json()
        products = data.get("result", {}).get("products", [])
        if not products:
            break

        all_items.extend(products)
        if len(products) < limit:
            break
        offset += limit
        time.sleep(0.3)

    if not all_items:
        return pd.DataFrame()

    df = pd.json_normalize(all_items)
    return df


def _fetch_offer_ids(product_ids: list[int]) -> dict[int, str]:
    """Получить offer_id по product_id через /v3/product/list.

    Returns:
        dict: {product_id: offer_id}
    """
    result = {}
    for i in range(0, len(product_ids), 1000):
        batch = product_ids[i:i + 1000]
        resp = requests.post(
            f"{BASE}/v3/product/list",
            headers=HEADERS,
            json={"filter": {"product_id": batch}, "limit": 1000},
        )
        if resp.status_code != 200:
            print(f"  Ошибка product/list: {resp.status_code}")
            continue
        for it in resp.json().get("result", {}).get("items", []):
            pid = it.get("product_id", 0)
            result[pid] = it.get("offer_id", "")
        time.sleep(0.3)
    return result


def build_promos_data() -> dict:
    """Собирает все данные по акциям Ozon для дашборда.

    Returns:
        dict: {
            "actions": DataFrame — список акций,
            "details": {action_id: DataFrame} — товары в каждой акции
                       (объединение участников + кандидатов),
        }
    """
    actions = get_actions()
    if actions.empty:
        return {"actions": pd.DataFrame(), "details": {}}

    details = {}
    all_product_ids = set()

    for _, row in actions.iterrows():
        aid = int(row["id"])
        title = row.get("title", "")
        participating = row.get("participating_products_count", 0)
        potential = row.get("potential_products_count", 0)

        print(f"\n  Акция #{aid}: {title} (участвуют: {participating}, кандидаты: {potential})")

        # Участвующие товары
        in_action = get_action_products(aid)
        if not in_action.empty:
            in_action["in_action"] = True

        # Кандидаты
        candidates = get_action_candidates(aid)
        if not candidates.empty:
            candidates["in_action"] = False

        # Объединяем
        parts = [df for df in [in_action, candidates] if not df.empty]
        if parts:
            merged = pd.concat(parts, ignore_index=True)
            details[aid] = merged
            if "id" in merged.columns:
                all_product_ids.update(merged["id"].tolist())
        else:
            details[aid] = pd.DataFrame()

        time.sleep(0.3)

    # Получаем offer_id по product_id
    if all_product_ids:
        print(f"\n  Загрузка offer_id для {len(all_product_ids)} товаров...")
        pid_to_offer = _fetch_offer_ids(list(all_product_ids))
        print(f"  Получено {len(pid_to_offer)} offer_id")
        for aid, df in details.items():
            if not df.empty and "id" in df.columns:
                df["offer_id"] = df["id"].map(pid_to_offer).fillna("")

    print(f"\n  Ozon акции загружены: {len(actions)} акций, {len(all_product_ids)} уникальных товаров")
    return {"actions": actions, "details": details}
