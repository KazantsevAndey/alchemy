"""
Управление акциями и ценами Wildberries — модуль для Streamlit-дашборда.

API:
  - Календарь акций: dp-calendar-api.wildberries.ru
  - Цены и скидки:   discounts-prices-api.wildberries.ru

Для автоакций WB API не отдаёт planPrice по товарам (422).
Обходной путь: скачать XLSX из кабинета WB и положить в promo_data/.
Формат имени файла: promo_data/<ID акции>_<название>.xlsx
Внутри XLSX ожидаются колонки: Артикул WB (nmID) и Плановая цена (planPrice).

Все функции возвращают DataFrame.
Мутирующие функции (add/remove/set_price) возвращают DataFrame со статусом операции.
"""

import pandas as pd
import requests
import time
import os
import glob as glob_mod
from datetime import datetime, timedelta

_HEADERS_CACHE = {}


def _get_creds(creds):
    if creds is not None:
        return creds
    try:
        from config import WB_API_KEY
        return {"WB_API_KEY": WB_API_KEY}
    except ImportError:
        return {}


def _wb_headers(creds=None):
    c = _get_creds(creds)
    return {
        "Authorization": c["WB_API_KEY"],
        "Content-Type": "application/json",
    }

PROMO_BASE = "https://dp-calendar-api.wildberries.ru"
PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
STATS_BASE = "https://statistics-api.wildberries.ru"

# Макс. 10 запросов за 6 сек — ставим 0.6с между запросами для безопасности
_RATE_LIMIT_DELAY = 0.7


_RATE_LIMIT_BACKOFF = (6, 30, 65)

# Circuit breaker: после устойчивого 429 пропускаем последующие WB-запросы 10 мин.
_RATE_LIMIT_COOLDOWN_SEC = 600
_rate_limited_until = 0.0
_cached_429_resp = None


def _circuit_open() -> bool:
    return _cached_429_resp is not None and time.monotonic() < _rate_limited_until


def _trip_circuit(resp: requests.Response):
    global _rate_limited_until, _cached_429_resp
    if _cached_429_resp is None:
        print(
            f"  ⚠️ WB API упорно отдаёт 429 — пропускаю WB-запросы "
            f"{_RATE_LIMIT_COOLDOWN_SEC // 60} мин"
        )
    _cached_429_resp = resp
    _rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN_SEC


def _reset_circuit():
    global _rate_limited_until, _cached_429_resp
    _cached_429_resp = None
    _rate_limited_until = 0.0


def _safe_get(url: str, params: dict | None = None, creds=None) -> requests.Response:
    if _circuit_open():
        return _cached_429_resp
    headers = _wb_headers(creds)
    time.sleep(_RATE_LIMIT_DELAY)
    resp = requests.get(url, headers=headers, params=params)
    for wait in _RATE_LIMIT_BACKOFF:
        if resp.status_code != 429:
            break
        print(f"  Rate limit, жду {wait} сек...")
        time.sleep(wait)
        resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 429:
        _trip_circuit(resp)
    else:
        _reset_circuit()
    return resp


def _safe_post(url: str, json_data: dict, creds=None) -> requests.Response:
    if _circuit_open():
        return _cached_429_resp
    headers = _wb_headers(creds)
    time.sleep(_RATE_LIMIT_DELAY)
    resp = requests.post(url, headers=headers, json=json_data)
    for wait in _RATE_LIMIT_BACKOFF:
        if resp.status_code != 429:
            break
        print(f"  Rate limit, жду {wait} сек...")
        time.sleep(wait)
        resp = requests.post(url, headers=headers, json=json_data)
    if resp.status_code == 429:
        _trip_circuit(resp)
    else:
        _reset_circuit()
    return resp


# ── Акции ────────────────────────────────────────────────────────────────

def get_promotions(all_promo: bool = False, creds=None) -> pd.DataFrame:
    """Получает список акций (календарных).

    Args:
        all_promo: True — все акции, False — только доступные для участия.

    Returns:
        DataFrame: id, name, startDateTime, endDateTime, type и др.
    """
    now = datetime.now()
    params = {
        "startDateTime": (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z"),
        "endDateTime": (now + timedelta(days=90)).strftime("%Y-%m-%dT23:59:59Z"),
        "allPromo": str(all_promo).lower(),
        "limit": 1000,
        "offset": 0,
    }

    resp = _safe_get(f"{PROMO_BASE}/api/v1/calendar/promotions", params=params, creds=creds)
    if resp.status_code != 200:
        print(f"Ошибка получения акций: {resp.status_code} — {resp.text[:300]}")
        return pd.DataFrame()

    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "promotions" in data:
        data = data["promotions"]

    if not data:
        print("Акций не найдено")
        return pd.DataFrame()

    df = pd.json_normalize(data)
    print(f"Акций: {len(df)}")
    return df


def get_promotion_details(promotion_ids: list[int] | int, creds=None) -> pd.DataFrame:
    """Детали акции: условия, бусты, даты.

    Args:
        promotion_ids: один ID или список ID акций.

    Returns:
        DataFrame с полями акций.
    """
    if isinstance(promotion_ids, int):
        promotion_ids = [promotion_ids]

    params = [("promotionIDs", pid) for pid in promotion_ids]
    resp = _safe_get(
        f"{PROMO_BASE}/api/v1/calendar/promotions/details",
        params=params,
        creds=creds,
    )
    if resp.status_code != 200:
        print(f"Ошибка деталей акций: {resp.status_code} — {resp.text[:300]}")
        return pd.DataFrame()

    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "promotions" in data:
        data = data["promotions"]

    return pd.json_normalize(data if isinstance(data, list) else [data])


def get_promo_products(promotion_id: int, in_action: bool | None = None,
                       creds=None) -> pd.DataFrame:
    """Товары для акции (наши SKU).

    Делает два запроса (inAction=true + inAction=false) и склеивает,
    если in_action не задан. Для авто-акций (type=auto) API возвращает 422 —
    товары авто-акций не управляются через этот endpoint.

    Args:
        promotion_id: ID акции (только regular-акции)
        in_action: None — все, True — только участвующие, False — только не участвующие

    Returns:
        DataFrame: nmID, planPrice, planDiscount, inAction и др.
        - inAction=True → товар уже участвует
        - planPrice → цена, необходимая для участия
    """
    targets = [in_action] if in_action is not None else [True, False]
    all_items = []

    for ia in targets:
        offset = 0
        limit = 1000
        while True:
            params = {
                "promotionID": promotion_id,
                "inAction": str(ia).lower(),
                "limitNomenclature": limit,
                "offset": offset,
            }
            resp = _safe_get(
                f"{PROMO_BASE}/api/v1/calendar/promotions/nomenclatures",
                params=params,
                creds=creds,
            )
            if resp.status_code == 422:
                print(f"  Акция {promotion_id}: авто-акция (товары не управляются через API)")
                return pd.DataFrame()
            if resp.status_code != 200:
                print(f"  Ошибка товаров акции {promotion_id}: {resp.status_code} — {resp.text[:200]}")
                break

            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            if isinstance(data, dict) and "nomenclatures" in data:
                items = data["nomenclatures"]
            elif isinstance(data, list):
                items = data
            else:
                items = []

            if not items:
                break

            # Помечаем inAction для объединённого результата
            for item in items:
                item["inAction"] = ia
            all_items.extend(items)

            if len(items) < limit:
                break
            offset += limit

    if not all_items:
        return pd.DataFrame()

    df = pd.json_normalize(all_items)
    in_count = df["inAction"].sum() if "inAction" in df.columns else 0
    print(f"  Акция {promotion_id}: {len(df)} товаров, {in_count} участвуют")
    return df


def add_to_promo(promotion_id: int, nm_ids: list[int], upload_now: bool = True) -> pd.DataFrame:
    """Добавляет товары в акцию.

    Args:
        promotion_id: ID акции
        nm_ids: список nmID для добавления
        upload_now: True — установить скидку сразу, False — при старте акции

    Returns:
        DataFrame со статусом операции.
    """
    resp = _safe_post(
        f"{PROMO_BASE}/api/v1/calendar/promotions/upload",
        json_data={
            "data": {
                "promotionID": promotion_id,
                "uploadNow": upload_now,
                "nomenclatures": nm_ids,
            }
        },
    )

    result = {
        "action": "add_to_promo",
        "promotion_id": promotion_id,
        "nm_ids_count": len(nm_ids),
        "status_code": resp.status_code,
        "success": resp.status_code == 200,
        "response": resp.text[:500],
    }
    if resp.status_code == 200:
        print(f"Добавлено {len(nm_ids)} товаров в акцию {promotion_id}")
    else:
        print(f"Ошибка добавления в акцию: {resp.status_code} — {resp.text[:300]}")

    return pd.DataFrame([result])


def remove_from_promo(promotion_id: int, nm_ids: list[int]) -> pd.DataFrame:
    """Убирает товары из акции.

    Args:
        promotion_id: ID акции
        nm_ids: список nmID для удаления

    Returns:
        DataFrame со статусом операции.
    """
    resp = _safe_post(
        f"{PROMO_BASE}/api/v1/calendar/promotions/detach",
        json_data={
            "data": {
                "promotionID": promotion_id,
                "nomenclatures": nm_ids,
            }
        },
    )

    result = {
        "action": "remove_from_promo",
        "promotion_id": promotion_id,
        "nm_ids_count": len(nm_ids),
        "status_code": resp.status_code,
        "success": resp.status_code == 200,
        "response": resp.text[:500],
    }
    if resp.status_code == 200:
        print(f"Убрано {len(nm_ids)} товаров из акции {promotion_id}")
    else:
        print(f"Ошибка удаления из акции: {resp.status_code} — {resp.text[:300]}")

    return pd.DataFrame([result])


# ── Остатки ──────────────────────────────────────────────────────────────

def get_stocks(creds=None) -> pd.DataFrame:
    """Получает остатки на складах WB (supplier/stocks).

    Returns:
        DataFrame: nmId, supplierArticle, warehouseName, quantity, quantityFull, Price, Discount и др.
    """
    resp = _safe_get(
        f"{STATS_BASE}/api/v1/supplier/stocks",
        params={"dateFrom": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")},
        creds=creds,
    )
    if resp.status_code != 200:
        print(f"Ошибка получения остатков: {resp.status_code} — {resp.text[:300]}")
        return pd.DataFrame()

    data = resp.json()
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    print(f"Остатки: {len(df)} записей, {df['nmId'].nunique()} nmID")
    return df


def get_stocks_summary(creds=None) -> pd.DataFrame:
    """Агрегированные остатки по nmId (суммарно по всем складам).

    Returns:
        DataFrame: nmId, supplierArticle, subject, brand, quantity, quantityFull, Price, Discount.
        Только позиции с quantity > 0.
    """
    stocks = get_stocks(creds=creds)
    if stocks.empty:
        return pd.DataFrame()

    agg = (
        stocks.groupby(["nmId", "supplierArticle", "subject", "brand"])
        .agg(
            quantity=("quantity", "sum"),
            quantityFull=("quantityFull", "sum"),
            inWayToClient=("inWayToClient", "sum"),
            inWayFromClient=("inWayFromClient", "sum"),
            Price=("Price", "first"),
            Discount=("Discount", "first"),
        )
        .reset_index()
    )
    in_stock = agg[agg["quantity"] > 0].sort_values("quantity", ascending=False).reset_index(drop=True)
    print(f"В наличии: {len(in_stock)} nmID из {len(agg)}")
    return in_stock


# ── Цены и скидки ───────────────────────────────────────────────────────

def get_prices(nm_ids: list[int] | None = None, only_in_stock: bool = False, creds=None) -> pd.DataFrame:
    """Получает текущие цены и скидки.

    Args:
        nm_ids: если указан — фильтрует по конкретным nmID,
                иначе загружает всё с пагинацией.
        only_in_stock: если True — оставляет только позиции с остатками > 0.

    Returns:
        DataFrame: nmID, vendorCode, price, discount, discountedPrice и др.
    """
    all_items = []
    offset = 0
    limit = 1000

    while True:
        params = {"limit": limit, "offset": offset}
        if nm_ids and len(nm_ids) == 1:
            params["filterNmID"] = nm_ids[0]

        resp = _safe_get(f"{PRICES_BASE}/api/v2/list/goods/filter", params=params,
                         creds=creds)
        if resp.status_code != 200:
            print(f"Ошибка получения цен: {resp.status_code} — {resp.text[:300]}")
            break

        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            goods = data["data"].get("listGoods", [])
        else:
            goods = []

        if not goods:
            break

        for item in goods:
            nm_id = item.get("nmID")
            vendor = item.get("vendorCode", "")
            for size in item.get("sizes", []):
                all_items.append({
                    "nmID": nm_id,
                    "vendorCode": vendor,
                    "sizeID": size.get("sizeID"),
                    "techSizeName": size.get("techSizeName", ""),
                    "price": size.get("price", 0),
                    "discount": size.get("discount", 0),
                    "discountedPrice": size.get("discountedPrice", 0),
                    "clubDiscountedPrice": size.get("clubDiscountedPrice", 0),
                    "clubDiscount": size.get("clubDiscount", 0),
                })

        if len(goods) < limit:
            break
        offset += limit

    if not all_items:
        return pd.DataFrame()

    df = pd.DataFrame(all_items)

    # Фильтрация если запрошены конкретные nmID (больше одного)
    if nm_ids and len(nm_ids) > 1:
        df = df[df["nmID"].isin(nm_ids)]

    # Только товары в наличии
    if only_in_stock:
        stocks = get_stocks_summary(creds=creds)
        if not stocks.empty:
            in_stock_ids = set(stocks["nmId"].tolist())
            before = len(df)
            df = df[df["nmID"].isin(in_stock_ids)].reset_index(drop=True)
            print(f"Фильтр по остаткам: {before} -> {len(df)} позиций")

    print(f"Цены: {len(df)} позиций ({df['nmID'].nunique()} nmID)")
    return df


def set_prices(updates: list[dict]) -> pd.DataFrame:
    """Устанавливает цены и скидки.

    Args:
        updates: список словарей, каждый вида:
            {"nmID": 123, "price": 999, "discount": 30}

    Returns:
        DataFrame со статусом операции.
    """
    resp = _safe_post(
        f"{PRICES_BASE}/api/v2/upload/task",
        json_data={"data": updates},
    )

    result = {
        "action": "set_prices",
        "items_count": len(updates),
        "status_code": resp.status_code,
        "success": resp.status_code in (200, 208),
        "response": resp.text[:500],
    }

    if resp.status_code == 200:
        task_id = resp.json().get("data", {}).get("id", "?")
        result["task_id"] = task_id
        print(f"Цены установлены: {len(updates)} товаров, задача #{task_id}")
    elif resp.status_code == 208:
        print("Такая загрузка уже существует (208)")
    else:
        print(f"Ошибка установки цен: {resp.status_code} — {resp.text[:300]}")

    return pd.DataFrame([result])


def set_price_single(nm_id: int, price: int, discount: int) -> pd.DataFrame:
    """Устанавливает цену и скидку для одного товара.

    Args:
        nm_id: Wildberries артикул
        price: базовая цена (до скидки)
        discount: скидка в процентах (0-99)

    Returns:
        DataFrame со статусом.
    """
    return set_prices([{"nmID": nm_id, "price": price, "discount": discount}])


def get_price_upload_status() -> pd.DataFrame:
    """Статус последних загрузок цен (обработанные)."""
    resp = _safe_get(f"{PRICES_BASE}/api/v2/history/tasks")
    if resp.status_code != 200:
        print(f"Ошибка статуса загрузок: {resp.status_code}")
        return pd.DataFrame()

    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not data:
        return pd.DataFrame()

    return pd.json_normalize(data if isinstance(data, list) else [data])


# ── Сводная таблица: акция + текущие цены ────────────────────────────────

def get_promo_with_prices(promotion_id: int) -> pd.DataFrame:
    """Сводная таблица для дашборда: товары акции + текущие цены.

    Returns:
        DataFrame с колонками:
        - nmID, inAction (участвует или нет)
        - planPrice, planDiscount (требования акции)
        - currentPrice, currentDiscount, currentDiscountedPrice (текущие)
        - priceDiff (разница текущей и требуемой цены)
    """
    promo = get_promo_products(promotion_id)
    if promo.empty:
        return pd.DataFrame()

    nm_ids = promo["nmID"].tolist() if "nmID" in promo.columns else []
    if not nm_ids:
        return promo

    prices = get_prices(nm_ids)

    if prices.empty:
        return promo

    # Берём одну строку на nmID (первый size)
    prices_dedup = (
        prices.sort_values("sizeID")
        .drop_duplicates(subset="nmID", keep="first")
        [["nmID", "vendorCode", "price", "discount", "discountedPrice"]]
        .rename(columns={
            "price": "currentPrice",
            "discount": "currentDiscount",
            "discountedPrice": "currentDiscountedPrice",
        })
    )

    merged = promo.merge(prices_dedup, on="nmID", how="left")

    if "planPrice" in merged.columns and "currentDiscountedPrice" in merged.columns:
        merged["priceDiff"] = merged["currentDiscountedPrice"] - merged["planPrice"]
    elif "planPrice" in merged.columns and "currentPrice" in merged.columns:
        merged["priceDiff"] = merged["currentPrice"] - merged["planPrice"]

    return merged


# ── Детали автоакций ──────────────────────────────────────────────────────

def get_auto_promo_details(promo_ids: list[int], creds=None) -> list[dict]:
    """Получает детали автоакций через /details endpoint.

    Returns:
        Список словарей с полями: id, name, type, startDateTime, endDateTime,
        inPromoActionTotal, notInPromoActionTotal, exceptionProductsCount, ranging.
    """
    if not promo_ids:
        return []
    params = [("promotionIDs", pid) for pid in promo_ids]
    resp = _safe_get(
        f"{PROMO_BASE}/api/v1/calendar/promotions/details",
        params=params,
        creds=creds,
    )
    if resp.status_code != 200:
        print(f"Ошибка деталей автоакций: {resp.status_code}")
        return []
    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "promotions" in data:
        data = data["promotions"]
    return data if isinstance(data, list) else []


# ── Загрузка данных автоакций из XLSX (из кабинета WB) ────────────────────

PROMO_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promo_data")


def load_auto_promo_xlsx(promo_id: int | None = None) -> dict[int, pd.DataFrame]:
    """Загружает XLSX-файлы с товарами автоакций из папки promo_data/.

    Формат файла: любой .xlsx в promo_data/.
    Скрипт автоматически ищет колонки с nmID и planPrice (по ключевым словам).

    Args:
        promo_id: если указан — загружает только файл для этой акции.

    Returns:
        dict: {promo_id: DataFrame с колонками nmID, planPrice, ...}
        Если promo_id не определяется из имени файла, используется хеш имени.
    """
    if not os.path.isdir(PROMO_DATA_DIR):
        return {}

    files = glob_mod.glob(os.path.join(PROMO_DATA_DIR, "*.xlsx"))
    if not files:
        return {}

    result = {}
    for fpath in sorted(files):
        fname = os.path.basename(fpath)

        # Пытаемся извлечь ID акции из имени файла (например "2116_Скидки.xlsx")
        pid = None
        parts = fname.split("_", 1)
        if parts[0].isdigit():
            pid = int(parts[0])

        if promo_id is not None and pid != promo_id:
            continue

        try:
            df = pd.read_excel(fpath)
        except Exception as e:
            print(f"  Ошибка чтения {fname}: {e}")
            continue

        if df.empty:
            continue

        # Ищем колонку с nmID (артикул WB)
        nm_col = _find_column(df, ["nmid", "nm id", "артикул wb", "артикул wб",
                                    "wb артикул", "номенклатура", "nm_id", "артикул вб"])
        # Ищем колонку с planPrice (плановая цена)
        price_col = _find_column(df, ["plan", "плановая цена", "цена акц", "акционная цена",
                                       "цена для акции", "план цена", "planprice",
                                       "цена участия", "рекомендуемая цена"])

        if nm_col is None or price_col is None:
            # Попробуем по индексу: первая числовая — nmID, вторая — price
            num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
            if len(num_cols) >= 2 and nm_col is None and price_col is None:
                nm_col = num_cols[0]
                price_col = num_cols[1]
                print(f"  {fname}: авто-определение колонок: nmID={nm_col}, planPrice={price_col}")
            else:
                print(f"  {fname}: не найдены колонки nmID/planPrice. Колонки: {list(df.columns)}")
                continue

        promo_df = pd.DataFrame({
            "nmID": pd.to_numeric(df[nm_col], errors="coerce").dropna().astype(int),
            "planPrice": pd.to_numeric(df[price_col], errors="coerce"),
        }).dropna()

        if promo_df.empty:
            continue

        if pid is None:
            pid = abs(hash(fname)) % 100000

        result[pid] = promo_df
        print(f"  {fname} → акция #{pid}: {len(promo_df)} товаров с planPrice")

    return result


def _find_column(df: pd.DataFrame, keywords: list[str]) -> str | None:
    """Ищет колонку в DataFrame по ключевым словам (case-insensitive, partial match)."""
    for col in df.columns:
        col_lower = str(col).lower().strip()
        for kw in keywords:
            if kw in col_lower:
                return col
    return None


# ── Сводный дашборд: артикул + текущая цена/маржа + цены акций ────────────

def build_dashboard(creds=None, price_path=None, price_df=None) -> pd.DataFrame:
    """Сводная таблица для дашборда.

    Колонки:
      Артикул, Название, Остаток, Себестоимость, Цена сейчас, Маржа сейчас %,
      [Цена: <regular-акция1>, ...],
      [Авто: <auto-акция1> (товаров: N, boost: X%), ...]

    Только товары в наличии.
    Для regular-акций — planPrice по каждому товару.
    Для auto-акций — сводная информация (количество товаров, boost),
    т.к. WB API не отдаёт planPrice для автоакций.
    """
    from utils.price_loader import load_price, build_cost_map

    # 1. Себестоимость
    if price_df is None:
        if price_path is None:
            try:
                from config import PRICE_FILE
                price_path = PRICE_FILE
            except ImportError:
                price_path = None
        if price_path:
            price_df = load_price(price_path)
        else:
            print("  Прайс не найден — себестоимость не будет учтена")
            price_df = pd.DataFrame(
                columns=["Артикул", "Наименование", "Цена в рублях"])
    cost_map = build_cost_map(price_df, key_col="Артикул")
    name_map = dict(zip(
        price_df["Артикул"].astype(str).str.strip(),
        price_df["Наименование"],
    ))

    # 2. Остатки (только в наличии)
    stocks = get_stocks_summary(creds=creds)
    if stocks.empty:
        print("Нет товаров в наличии")
        return pd.DataFrame()

    # 3. Текущие цены
    in_stock_ids = stocks["nmId"].tolist()
    prices = get_prices(nm_ids=in_stock_ids, creds=creds)
    if prices.empty:
        print("Не удалось получить цены")
        return pd.DataFrame()

    # Одна строка на nmID
    prices = (
        prices.sort_values("sizeID")
        .drop_duplicates(subset="nmID", keep="first")
    )

    # 4. Собираем базу
    df = stocks[["nmId", "supplierArticle", "quantity"]].copy()
    df = df.merge(
        prices[["nmID", "discountedPrice"]],
        left_on="nmId", right_on="nmID", how="left",
    ).drop(columns=["nmID"])

    df["Артикул"] = df["supplierArticle"]
    df["Название"] = df["supplierArticle"].map(name_map).fillna("")
    df["Остаток"] = df["quantity"]
    df["Себестоимость"] = df["supplierArticle"].map(cost_map).fillna(0)
    df["Цена сейчас"] = df["discountedPrice"].fillna(0).round(0).astype(int)
    df["Маржа сейчас %"] = (
        (df["Цена сейчас"] - df["Себестоимость"]) / df["Цена сейчас"] * 100
    ).where(df["Цена сейчас"] > 0, 0).round(1)

    result = df[["Артикул", "Название", "Остаток", "Себестоимость", "Цена сейчас", "Маржа сейчас %"]].copy()

    # 5. Получаем ВСЕ акции
    promos = get_promotions(all_promo=True, creds=creds)
    if promos.empty or "type" not in promos.columns:
        return result.sort_values("Остаток", ascending=False).reset_index(drop=True)

    # 5a. Regular-акции — получаем planPrice по каждому товару
    regular = promos[promos["type"] == "regular"]
    promo_count = 0
    for _, promo_row in regular.iterrows():
        pid = int(promo_row["id"])
        pname = str(promo_row.get("name", f"Акция {pid}"))

        products = get_promo_products(pid, creds=creds)
        if products.empty:
            continue

        if "nmID" not in products.columns or "planPrice" not in products.columns:
            continue

        promo_prices = products[products["nmID"].isin(in_stock_ids)][["nmID", "planPrice"]].copy()
        if promo_prices.empty:
            continue

        col_name = f"Цена: {pname}"
        promo_prices = promo_prices.rename(columns={"planPrice": col_name})
        result = result.merge(
            promo_prices.merge(
                stocks[["nmId", "supplierArticle"]],
                left_on="nmID", right_on="nmId",
            )[["supplierArticle", col_name]],
            left_on="Артикул", right_on="supplierArticle", how="left",
        ).drop(columns=["supplierArticle"])
        promo_count += 1

    # 5b. Auto-акции — planPrice из XLSX файлов (скачанных из кабинета WB)
    auto = promos[promos["type"] == "auto"]
    if not auto.empty:
        auto_ids = auto["id"].astype(int).tolist()
        details = get_auto_promo_details(auto_ids, creds=creds)

        # Загружаем XLSX-данные
        print("\nЗагрузка данных автоакций из promo_data/...")
        xlsx_data = load_auto_promo_xlsx()

        for d in details:
            pid = d["id"]
            pname = d.get("name", f"Автоакция {pid}")
            in_total = d.get("inPromoActionTotal", 0)
            not_in_total = d.get("notInPromoActionTotal", 0)
            total = in_total + not_in_total
            if total == 0:
                continue

            col_name = f"Цена: {pname}"

            if pid in xlsx_data:
                # Есть XLSX с planPrice — подтягиваем по товарам
                promo_prices = xlsx_data[pid]
                promo_prices = promo_prices[promo_prices["nmID"].isin(in_stock_ids)].copy()
                if not promo_prices.empty:
                    promo_prices = promo_prices.rename(columns={"planPrice": col_name})
                    result = result.merge(
                        promo_prices.merge(
                            stocks[["nmId", "supplierArticle"]],
                            left_on="nmID", right_on="nmId",
                        )[["supplierArticle", col_name]],
                        left_on="Артикул", right_on="supplierArticle", how="left",
                    ).drop(columns=["supplierArticle"])
                    promo_count += 1
                    print(f"  ✓ {pname}: {len(promo_prices)} товаров с planPrice")
                else:
                    print(f"  {pname}: XLSX загружен, но нет пересечения с остатками")
            else:
                # Нет XLSX — показываем сводку
                max_boost = max((r.get("boost", 0) for r in d.get("ranging", [])), default=0)
                start = d.get("startDateTime", "")[:10]
                end = d.get("endDateTime", "")[:10]
                info = f"[нет XLSX] товаров: {total}, boost: {max_boost}%"
                result[col_name] = info
                promo_count += 1
                print(f"  ⚠ {pname}: нет XLSX в promo_data/{pid}_*.xlsx")

    print(f"\nАкций в дашборде: {promo_count}")
    return result.sort_values("Остаток", ascending=False).reset_index(drop=True)


# ── CLI-интерфейс для тестирования ───────────────────────────────────────

def main():
    import warnings
    warnings.filterwarnings("ignore")

    pd.set_option("display.max_columns", 30)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 45)

    print("=" * 60)
    print("WB АКЦИИ И ЦЕНЫ — ДАШБОРД")
    print("=" * 60)

    dashboard = build_dashboard()

    if dashboard.empty:
        print("Не удалось собрать дашборд")
        return

    print(f"\n{'=' * 60}")
    print(f"СВОДНАЯ ТАБЛИЦА: {len(dashboard)} товаров в наличии")
    print(f"{'=' * 60}\n")
    print(dashboard.to_string(index=False))

    print("\nГотово.")


def load_cached_dashboard():
    """Загрузить дашборд из cache/."""
    from data_loader import load as _ld
    return _ld("wb_promos_dash")


if __name__ == "__main__":
    main()
