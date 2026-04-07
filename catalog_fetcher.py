"""
Fetch product catalogs from connected marketplaces.

Returns unified list of dicts with keys:
  article, name, ozon_product_id, ozon_sku, wb_nm_id, ym_market_sku
"""

import requests
import time


# ── Ozon ─────────────────────────────────────────────────────────────

def fetch_ozon_catalog(creds: dict) -> list[dict]:
    """Fetch all products from Ozon via /v2/product/list + /v3/product/info/list."""
    client_id = creds.get("OZON_SELLER_CLIENT_ID", "")
    api_key = creds.get("OZON_SELLER_API_KEY_V2", creds.get("OZON_SELLER_API_KEY", ""))
    if not client_id or not api_key:
        return []

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    base = "https://api-seller.ozon.ru"

    # Step 1: get all product_ids
    product_ids = []
    last_id = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        resp = requests.post(f"{base}/v2/product/list", headers=headers, json=body)
        if resp.status_code != 200:
            print(f"  Ozon /v2/product/list ошибка: {resp.status_code}")
            break
        data = resp.json().get("result", {})
        items = data.get("items", [])
        if not items:
            break
        product_ids.extend([it["product_id"] for it in items])
        last_id = data.get("last_id", "")
        if not last_id or len(items) < 1000:
            break
        time.sleep(0.3)

    if not product_ids:
        return []
    print(f"  Ozon: {len(product_ids)} товаров найдено")

    # Step 2: get product info in batches
    result = []
    for i in range(0, len(product_ids), 1000):
        batch = product_ids[i:i + 1000]
        resp = requests.post(
            f"{base}/v3/product/info/list",
            headers=headers,
            json={"product_id": batch},
        )
        if resp.status_code != 200:
            print(f"  Ozon info ошибка: {resp.status_code}")
            continue
        for it in resp.json().get("items", []):
            offer_id = str(it.get("offer_id", "")).strip()
            if not offer_id:
                continue
            # Get FBO SKU
            ozon_sku = str(it.get("fbo_sku", "")) if it.get("fbo_sku") else ""
            if not ozon_sku:
                # Try sources
                for src in it.get("sources", []):
                    if src.get("source") == "fbo":
                        ozon_sku = str(src.get("sku", ""))
                        break
            result.append({
                "article": offer_id,
                "name": it.get("name", ""),
                "ozon_product_id": it.get("id") or it.get("product_id"),
                "ozon_sku": ozon_sku,
            })
        time.sleep(0.3)

    print(f"  Ozon: {len(result)} товаров загружено")
    return result


# ── Wildberries ──────────────────────────────────────────────────────

def fetch_wb_catalog(creds: dict) -> list[dict]:
    """Fetch all cards from WB via /content/v2/get/cards/list."""
    api_key = creds.get("WB_API_KEY", "")
    if not api_key:
        return []

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    base = "https://content-api.wildberries.ru"

    result = []
    cursor = {"limit": 100}
    while True:
        body = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
        resp = requests.post(f"{base}/content/v2/get/cards/list", headers=headers, json=body)
        if resp.status_code != 200:
            print(f"  WB cards ошибка: {resp.status_code} — {resp.text[:200]}")
            break
        data = resp.json().get("cards", [])
        if not data:
            break
        for card in data:
            vendor_code = str(card.get("vendorCode", "")).strip()
            if not vendor_code:
                continue
            result.append({
                "article": vendor_code,
                "name": card.get("title", ""),
                "wb_nm_id": card.get("nmID"),
            })
        # Cursor pagination
        cursor_data = resp.json().get("cursor", {})
        if not cursor_data.get("total", 0) or len(data) < 100:
            break
        cursor = {
            "limit": 100,
            "updatedAt": cursor_data.get("updatedAt", ""),
            "nmID": cursor_data.get("nmID", 0),
        }
        time.sleep(0.7)

    print(f"  WB: {len(result)} товаров загружено")
    return result


# ── Yandex Market ────────────────────────────────────────────────────

def fetch_ym_catalog(creds: dict) -> list[dict]:
    """Fetch all offers from YM via /businesses/{id}/offer-mappings."""
    api_key = creds.get("YM_API_KEY", "")
    business_id = creds.get("YM_BUSINESS_ID", "")
    if not api_key or not business_id:
        return []

    headers = {
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    base = "https://api.partner.market.yandex.ru"

    result = []
    page_token = None
    while True:
        body = {"limit": 200}
        if page_token:
            body["page_token"] = page_token
        resp = requests.post(
            f"{base}/businesses/{business_id}/offer-mappings",
            headers=headers, json=body,
        )
        if resp.status_code != 200:
            print(f"  YM offer-mappings ошибка: {resp.status_code} — {resp.text[:200]}")
            break
        data = resp.json().get("result", {})
        mappings = data.get("offerMappings", [])
        if not mappings:
            break
        for m in mappings:
            offer = m.get("offer", {})
            shop_sku = str(offer.get("offerId", "")).strip()
            if not shop_sku:
                continue
            mapping = m.get("mapping", {})
            result.append({
                "article": shop_sku,
                "name": offer.get("name", ""),
                "ym_market_sku": str(mapping.get("marketSku", "")) if mapping.get("marketSku") else "",
            })
        paging = data.get("paging", {})
        page_token = paging.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.5)

    print(f"  YM: {len(result)} товаров загружено")
    return result


# ── Merge ────────────────────────────────────────────────────────────

def merge_catalogs(ozon: list[dict], wb: list[dict], ym: list[dict]) -> list[dict]:
    """Merge catalogs by article. Returns unified list."""
    by_article = {}

    for item in ozon:
        art = item["article"]
        by_article[art] = {
            "article": art,
            "name": item.get("name", ""),
            "ozon_product_id": item.get("ozon_product_id"),
            "ozon_sku": item.get("ozon_sku"),
            "wb_nm_id": None,
            "ym_market_sku": None,
        }

    for item in wb:
        art = item["article"]
        if art in by_article:
            by_article[art]["wb_nm_id"] = item.get("wb_nm_id")
            if not by_article[art]["name"]:
                by_article[art]["name"] = item.get("name", "")
        else:
            by_article[art] = {
                "article": art,
                "name": item.get("name", ""),
                "ozon_product_id": None,
                "ozon_sku": None,
                "wb_nm_id": item.get("wb_nm_id"),
                "ym_market_sku": None,
            }

    for item in ym:
        art = item["article"]
        if art in by_article:
            by_article[art]["ym_market_sku"] = item.get("ym_market_sku")
            if not by_article[art]["name"]:
                by_article[art]["name"] = item.get("name", "")
        else:
            by_article[art] = {
                "article": art,
                "name": item.get("name", ""),
                "ozon_product_id": None,
                "ozon_sku": None,
                "wb_nm_id": None,
                "ym_market_sku": item.get("ym_market_sku"),
            }

    return sorted(by_article.values(), key=lambda x: x["article"])
