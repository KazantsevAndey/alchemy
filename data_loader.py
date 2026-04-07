"""
Cache manager for Alchemy dashboard.

Скачивает все данные с маркетплейсов, сохраняет в cache/ как pickle.
Дашборд читает только из кэша — страницы переключаются мгновенно.

Supports per-user cache directories (data/user_{id}/cache/)
or legacy global cache/ dir.

Usage:
  python data_loader.py                # обновить весь кэш (legacy)
  from data_loader import load, cache_age_minutes
"""

import pickle
from pathlib import Path
from datetime import datetime, timedelta

# Legacy global cache (for CLI / backward compat)
CACHE_DIR = Path("cache")
META_FILE = CACHE_DIR / "_meta.pkl"


# ── Per-user cache dirs ──────────────────────────────────────────────────

def _user_cache_dir(user_id=None) -> Path:
    if user_id is not None:
        p = Path(f"data/user_{user_id}/cache")
        p.mkdir(parents=True, exist_ok=True)
        return p
    return CACHE_DIR


def _user_meta_file(user_id=None) -> Path:
    return _user_cache_dir(user_id) / "_meta.pkl"


# ── Утилиты кэша ─────────────────────────────────────────────────────────

def save(key: str, data, user_id=None) -> None:
    """Сохранить объект в cache/<key>.pkl."""
    d = _user_cache_dir(user_id)
    d.mkdir(exist_ok=True)
    (d / f"{key}.pkl").write_bytes(pickle.dumps(data))


def load(key: str, user_id=None):
    """Загрузить объект из cache/<key>.pkl. None если нет."""
    p = _user_cache_dir(user_id) / f"{key}.pkl"
    return pickle.loads(p.read_bytes()) if p.exists() else None


def cache_age_minutes(user_id=None) -> float | None:
    """Возраст кэша в минутах. None если кэша нет."""
    mf = _user_meta_file(user_id)
    if not mf.exists():
        return None
    meta = pickle.loads(mf.read_bytes())
    return (datetime.now() - meta["timestamp"]).total_seconds() / 60


def cache_timestamp(user_id=None) -> str | None:
    """Время обновления кэша (строка). None если кэша нет."""
    mf = _user_meta_file(user_id)
    if not mf.exists():
        return None
    meta = pickle.loads(mf.read_bytes())
    return meta["timestamp"].strftime("%H:%M %d.%m")


# ── Список ключей ────────────────────────────────────────────────────────

ALL_KEYS = [
    "price",
    "oz_tx_y", "oz_final_y", "oz_nach_y",
    "oz_tx_m", "oz_final_m", "oz_nach_m",
    "wb_df_y", "wb_sum_y", "wb_agg_y",
    "wb_df_m", "wb_sum_m", "wb_agg_m",
    "ym_margin_y", "ym_margin_m",
    "stock_turnover",
    "wb_promos_dash",
    "oz_prices",
    "wb_prices",
    "oz_promos",
]


def load_all(user_id=None) -> dict:
    """Загрузить все данные из кэша. Ключи — см. ALL_KEYS."""
    return {k: load(k, user_id) for k in ALL_KEYS}


# ── Обновление ────────────────────────────────────────────────────────────

def refresh_all_data(user_id=None, creds=None, price_path=None) -> dict:
    """Скачать ВСЕ данные с API и сохранить в cache/.

    Args:
        user_id: если указан — per-user cache dir
        creds: dict с API-ключами (если None — fallback на config.py)
        price_path: путь к прайсу (если None — fallback на config.PRICE_FILE)
    """
    cache_dir = _user_cache_dir(user_id)
    cache_dir.mkdir(exist_ok=True)

    from utils.price_loader import load_price
    from ozon_margin import (
        _iso_z, fetch_transactions,
        build_final as ozon_build_final,
        get_ads_by_sku as ozon_get_ads,
    )
    from wb_margin import (
        load_report as wb_load_report, prepare_df as wb_prepare_df,
        calc_summary as wb_calc_summary,
        calc_unit_economics as wb_calc_unit_economics,
        get_wb_ads,
    )
    from ozon_supply import get_stock_turnover
    from wb_promos import build_dashboard as wb_build_dashboard

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    month_start = yesterday.replace(day=1)
    dy = yesterday.strftime("%Y-%m-%d")
    dm = month_start.strftime("%Y-%m-%d")
    day_before = (yesterday - timedelta(days=1)).strftime("%Y-%m-%d")

    # Прайс
    if price_path is None:
        try:
            from config import PRICE_FILE
            price_path = PRICE_FILE
        except ImportError:
            price_path = None

    print("Загрузка прайса...")
    price = load_price(price_path)
    save("price", price, user_id)

    # ── Ozon вчера ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("OZON ВЧЕРА")
    print("=" * 60)
    oz_tx_y = fetch_transactions(_iso_z(yesterday), _iso_z(yesterday, True), "Вчера", creds)
    oz_ads_y = ozon_get_ads(dy, dy, "за вчера", creds)
    oz_final_y, oz_nach_y = ozon_build_final(oz_tx_y, price, oz_ads_y, "за вчера")
    for k, v in [("oz_tx_y", oz_tx_y), ("oz_final_y", oz_final_y), ("oz_nach_y", oz_nach_y)]:
        save(k, v, user_id)

    # ── Ozon месяц ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("OZON МЕСЯЦ")
    print("=" * 60)
    oz_tx_m = fetch_transactions(_iso_z(month_start), _iso_z(yesterday, True), "Месяц", creds)
    oz_ads_m = ozon_get_ads(dm, dy, "за месяц", creds)
    oz_final_m, oz_nach_m = ozon_build_final(oz_tx_m, price, oz_ads_m, "за месяц")
    for k, v in [("oz_tx_m", oz_tx_m), ("oz_final_m", oz_final_m), ("oz_nach_m", oz_nach_m)]:
        save(k, v, user_id)

    # ── WB вчера ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("WB ВЧЕРА")
    print("=" * 60)
    wb_all = wb_prepare_df(wb_load_report(dm, dy, creds=creds))
    yesterday_date = yesterday.date()
    wb_df_y = wb_all[wb_all["sale_date"] == yesterday_date].copy()
    wb_ads_y = get_wb_ads(day_before, day_before, "за вчера", creds)
    wb_sum_y = wb_calc_summary(wb_df_y, "Вчера")
    wb_agg_y = wb_calc_unit_economics(wb_df_y, price, wb_ads_y)
    for k, v in [("wb_df_y", wb_df_y), ("wb_sum_y", wb_sum_y), ("wb_agg_y", wb_agg_y)]:
        save(k, v, user_id)

    # ── WB месяц ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("WB МЕСЯЦ")
    print("=" * 60)
    wb_df_m = wb_all
    wb_ads_m = get_wb_ads(dm, day_before, "за месяц", creds)
    wb_sum_m = wb_calc_summary(wb_df_m, "Месяц")
    wb_agg_m = wb_calc_unit_economics(wb_df_m, price, wb_ads_m)
    for k, v in [("wb_df_m", wb_df_m), ("wb_sum_m", wb_sum_m), ("wb_agg_m", wb_agg_m)]:
        save(k, v, user_id)

    # ── YM Маржа ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("YM МАРЖА")
    print("=" * 60)
    from ym_margin import load_services_report, calc_margin as ym_calc_margin
    from utils.price_loader import build_cost_map
    ym_cost_map = build_cost_map(price)

    print("YM: данные за вчера...")
    ym_rev_y, ym_costs_y, ym_totals_y = load_services_report(dy, dy, creds)
    ym_R_y = ym_calc_margin(ym_rev_y, ym_costs_y, ym_totals_y,
                             ym_cost_map, f"YM Вчера ({dy})")
    save("ym_margin_y", {"R": ym_R_y, "totals": ym_totals_y}, user_id)

    print("YM: данные за месяц...")
    ym_rev_m, ym_costs_m, ym_totals_m = load_services_report(dm, dy, creds)
    ym_R_m = ym_calc_margin(ym_rev_m, ym_costs_m, ym_totals_m,
                             ym_cost_map, f"YM Месяц ({dm} — {dy})")
    save("ym_margin_m", {"R": ym_R_m, "totals": ym_totals_m}, user_id)

    # ── Остатки Ozon ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("ОСТАТКИ OZON")
    print("=" * 60)
    stock_turnover = get_stock_turnover(creds=creds)
    save("stock_turnover", stock_turnover, user_id)

    # ── Цены WB (дашборд акций) ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("ЦЕНЫ WB")
    print("=" * 60)
    wb_dash = wb_build_dashboard(creds=creds, price_path=price_path)
    save("wb_promos_dash", wb_dash, user_id)

    # ── Цены Ozon (только SKU с продажами за месяц) ────────────────
    import time as _time
    print("\n" + "=" * 60)
    print("ЦЕНЫ ДЛЯ СРАВНЕНИЯ")
    print("=" * 60)

    t0 = _time.time()
    oz_sold_arts = []
    if oz_final_m is not None and not oz_final_m.empty and "sku" in oz_final_m.columns:
        sku_to_art = dict(zip(
            price["Ozon SKU ID"].dropna().astype(int).astype(str),
            price["Артикул"].astype(str).str.strip(),
        ))
        oz_sold_arts = [sku_to_art[str(int(s))] for s in oz_final_m["sku"].unique()
                        if str(int(s)) in sku_to_art]
        oz_sold_arts = [a for a in oz_sold_arts if a and a != "nan"]
    print(f"  Ozon: {len(oz_sold_arts)} артикулов с продажами")

    from ozon_prices import get_ozon_prices
    oz_prices = get_ozon_prices(offer_ids=oz_sold_arts if oz_sold_arts else None, creds=creds)
    save("oz_prices", oz_prices, user_id)
    print(f"  Ozon цены загружены за {_time.time() - t0:.1f}с")

    # ── Цены WB (только SKU с продажами за месяц) ────────────────
    t0 = _time.time()
    wb_sold_nm_ids = []
    if wb_agg_m is not None and not wb_agg_m.empty and "nm_id" in wb_agg_m.columns:
        wb_sold_nm_ids = wb_agg_m["nm_id"].dropna().astype(int).unique().tolist()
    print(f"  WB: {len(wb_sold_nm_ids)} nmID с продажами")

    from wb_promos import get_prices as wb_get_prices
    wb_pr = wb_get_prices(nm_ids=wb_sold_nm_ids if wb_sold_nm_ids else None, creds=creds)
    if not wb_pr.empty:
        wb_pr = wb_pr.sort_values("sizeID").drop_duplicates(subset="nmID", keep="first")
    save("wb_prices", wb_pr, user_id)
    print(f"  WB цены загружены за {_time.time() - t0:.1f}с")

    # ── Акции Ozon ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("АКЦИИ OZON")
    print("=" * 60)
    t0 = _time.time()
    from ozon_promos import build_promos_data
    oz_promos = build_promos_data(creds=creds)
    save("oz_promos", oz_promos, user_id)
    print(f"  Ozon акции загружены за {_time.time() - t0:.1f}с")

    # ── Meta ──────────────────────────────────────────────────────────
    meta = {"timestamp": datetime.now(), "yesterday": dy, "month_start": dm}
    _user_meta_file(user_id).write_bytes(pickle.dumps(meta))

    print("\n" + "=" * 60)
    print(f"Кэш обновлён: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 60)
    return meta


if __name__ == "__main__":
    refresh_all_data()
