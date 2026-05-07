"""
Маржинальность Wildberries — автономный скрипт.

Вход:
  - Прайс (себестоимость) из локального Excel
  - WB Statistics API (reportDetailByPeriod)
  - WB Promotion API (рекламные расходы / ДРР по nm_id)

Выход:
  - wb_margin_yesterday.xlsx
  - wb_margin_month.xlsx

Формула (бьётся с ЛК WB):
  К перечислению = Продажи - Возвраты
  Итого к выплате = К перечислению - Логистика - Хранение - Удержания
                    - Стоимость лояльности - Баллы лояльности
  Маржа = (Итого к выплате - Себестоимость) / Итого к выплате
"""

import pandas as pd
import requests
import time
from datetime import datetime, timedelta

from utils.price_loader import load_price


def _get_creds(creds):
    if creds is not None:
        return creds
    try:
        from config import WB_API_KEY
        return {"WB_API_KEY": WB_API_KEY}
    except ImportError:
        return {}


def _wb_headers(creds):
    c = _get_creds(creds)
    return {
        "Authorization": c["WB_API_KEY"],
        "Content-Type": "application/json",
    }

NUMERIC_FIELDS = [
    "ppvz_for_pay", "delivery_rub", "storage_fee", "deduction",
    "cashback_commission_change", "cashback_amount", "penalty",
    "acceptance", "quantity",
]


# ── Реклама (ДРР) через Promotion API ───────────────────────────────────

def _get_campaign_ids(creds=None) -> list[int]:
    """ID рекламных кампаний, у которых может быть расход в текущем периоде.

    WB-status: 9=активна, 11=на паузе, 7=архив, 4=готова к запуску.
    Архивные кампании в текущем месяце расход почти не приносят, поэтому
    их пропускаем — это сокращает число запросов в /adv/v3/fullstats
    в разы (1 req/min — основной bottleneck).
    """
    headers = _wb_headers(creds)
    resp = requests.get(
        "https://advert-api.wildberries.ru/adv/v1/promotion/count",
        headers=headers,
    )
    if resp.status_code != 200:
        print(f"  Ошибка получения кампаний: {resp.status_code}")
        return []

    data = resp.json()
    ids = []
    skipped_archived = 0
    for group in data.get("adverts") or []:
        status = group.get("status")
        if status not in (9, 11, 4):  # активные, пауза, готовые к запуску
            skipped_archived += sum(1 for it in (group.get("advert_list") or [])
                                    if it.get("advertId"))
            continue
        for item in group.get("advert_list") or []:
            adv_id = item.get("advertId")
            if adv_id:
                ids.append(adv_id)

    print(f"  Рекламных кампаний: {len(ids)} (пропущено архивных: {skipped_archived})")
    return ids


def _fetch_fullstats(campaign_ids: list[int], date_from: str, date_to: str,
                     creds=None) -> pd.DataFrame:
    """Загружает полную статистику по nm_id из /adv/v3/fullstats.

    Возвращает DataFrame с колонками [nm_id, adv_sum].
    """
    headers = _wb_headers(creds)
    all_rows = []
    # WB /adv/v3/fullstats: GET-параметр ids — длинные пакеты дают 400.
    # Колаб использует 10 — проверено и работает. 100 = слишком много.
    batch_size = 10
    total_batches = (len(campaign_ids) - 1) // batch_size + 1
    failed_batches = 0

    for i in range(0, len(campaign_ids), batch_size):
        batch = campaign_ids[i : i + batch_size]
        batch_num = i // batch_size + 1
        ids_str = ",".join(str(x) for x in batch)

        resp = requests.get(
            "https://advert-api.wildberries.ru/adv/v3/fullstats",
            headers=headers,
            params={"ids": ids_str, "beginDate": date_from, "endDate": date_to},
        )

        if resp.status_code == 429:
            print(f"    Пачка {batch_num}/{total_batches}: rate limit, жду 65с...")
            time.sleep(65)
            resp = requests.get(
                "https://advert-api.wildberries.ru/adv/v3/fullstats",
                headers=headers,
                params={"ids": ids_str, "beginDate": date_from, "endDate": date_to},
            )

        if resp.status_code != 200:
            failed_batches += 1
            print(f"    Пачка {batch_num}/{total_batches}: ошибка {resp.status_code}")
            time.sleep(65)
            continue

        campaigns = resp.json()
        if not isinstance(campaigns, list):
            failed_batches += 1
            time.sleep(65)
            continue

        for campaign in campaigns:
            for day in campaign.get("days") or []:
                for app in day.get("apps") or []:
                    for nm in app.get("nms") or []:
                        nm_id = nm.get("nmId", 0)
                        adv_sum = nm.get("sum", 0)
                        if nm_id and adv_sum:
                            all_rows.append({"nm_id": nm_id, "adv_sum": adv_sum})

        print(f"    Пачка {batch_num}/{total_batches}: ок")
        time.sleep(65)

    if failed_batches == total_batches:
        raise RuntimeError(
            f"WB /adv/v3/fullstats: все {total_batches} батчей упали ({date_from} → {date_to})"
        )
    if failed_batches:
        print(f"  ⚠ {failed_batches}/{total_batches} батчей упало — ДРР неполный")

    if not all_rows:
        return pd.DataFrame(columns=["nm_id", "adv_sum"])

    df = pd.DataFrame(all_rows)
    result = df.groupby("nm_id", as_index=False)["adv_sum"].sum()
    return result


def get_wb_ads(date_from: str, date_to: str, period_name: str,
               creds=None, campaign_ids: list[int] | None = None) -> pd.DataFrame:
    """Получает рекламные расходы по nm_id за период.

    date_to не должен быть сегодняшним днём (API не отдаёт данные за текущий день).
    Если campaign_ids передан — пропускаем запрос к /adv/v1/promotion/count.
    Полезно когда get_wb_ads вызывается несколько раз подряд (вчера + месяц).
    """
    print(f"\nДРР {period_name}: {date_from} — {date_to}")

    if campaign_ids is None:
        campaign_ids = _get_campaign_ids(creds)
    if not campaign_ids:
        return pd.DataFrame(columns=["nm_id", "adv_sum"])

    result = _fetch_fullstats(campaign_ids, date_from, date_to, creds)
    total = result["adv_sum"].sum() if not result.empty else 0
    print(f"  Итого ДРР: {total:,.0f}, SKU с ДРР: {len(result)}")
    return result


# ── Загрузка отчёта ─────────────────────────────────────────────────────

def load_report(date_from: str, date_to: str, period: str = "daily",
                creds=None) -> pd.DataFrame:
    url = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"
    headers = _wb_headers(creds)
    all_data: list = []
    rrdid = 0
    page = 1

    while True:
        params = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": 100000,
            "rrdid": rrdid,
            "period": period,
        }
        resp = requests.get(url, headers=headers, params=params)

        retries = 0
        while resp.status_code == 429 and retries < 3:
            retries += 1
            print(f"  Rate limit, жду 65 сек... (попытка {retries}/3)")
            time.sleep(65)
            resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 429:
            raise RuntimeError(
                f"WB reportDetailByPeriod: rate limit не отпустил после 3 попыток "
                f"({date_from} → {date_to})"
            )

        if resp.status_code == 204 or not resp.content:
            break
        if resp.status_code != 200:
            raise RuntimeError(
                f"WB reportDetailByPeriod: ошибка {resp.status_code} "
                f"({date_from} → {date_to})"
            )

        data = resp.json()
        if not data:
            break

        all_data.extend(data)
        rrdid = data[-1].get("rrd_id", 0)
        print(f"  Страница {page}: +{len(data)} (всего {len(all_data)})")
        page += 1

        if len(data) < 100000:
            break
        time.sleep(65)

    return pd.DataFrame(all_data)


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    for f in NUMERIC_FIELDS:
        if f in df.columns:
            df[f] = pd.to_numeric(df[f], errors="coerce").fillna(0)
    df["sa_name"] = df["sa_name"].astype(str).str.strip()
    df["sale_date"] = pd.to_datetime(df["sale_dt"]).dt.date
    return df


# ── Сводка по аккаунту ──────────────────────────────────────────────────

def calc_summary(df: pd.DataFrame, label: str) -> dict:
    sales = df[df["supplier_oper_name"] == "Продажа"]
    returns = df[df["supplier_oper_name"] == "Возврат"]

    k_per = sales["ppvz_for_pay"].sum() - returns["ppvz_for_pay"].sum()
    logist = df["delivery_rub"].sum()
    storage = df["storage_fee"].sum()
    deduction = df["deduction"].sum()
    loyal_cost = df["cashback_commission_change"].sum()
    loyal_balls = df["cashback_amount"].sum()
    itogo = k_per - logist - storage - deduction - loyal_cost - loyal_balls

    qty_sold = int(sales["quantity"].sum())
    qty_ret = int(returns["quantity"].sum())

    print(f"\n{'=' * 70}")
    print(f"СВОДКА: {label}")
    print(f"{'=' * 70}")
    print(f"  К перечислению (прод-возвр): {k_per:>12,.2f}")
    print(f"  - Логистика:                 {logist:>12,.2f}")
    print(f"  - Хранение:                  {storage:>12,.2f}")
    print(f"  - Удержания:                 {deduction:>12,.2f}")
    print(f"  - Стоимость лояльности:      {loyal_cost:>12,.2f}")
    print(f"  - Баллы лояльности:          {loyal_balls:>12,.2f}")
    print(f"  = ИТОГО к выплате:           {itogo:>12,.2f}")
    print(f"  Продано: {qty_sold} шт, Возвратов: {qty_ret} шт")

    return {
        "k_per": k_per, "logist": logist, "storage": storage,
        "deduction": deduction, "loyal_cost": loyal_cost,
        "loyal_balls": loyal_balls, "itogo": itogo,
        "qty_sold": qty_sold, "qty_ret": qty_ret,
    }


# ── Юнит-экономика по SKU ───────────────────────────────────────────────

def calc_unit_economics(df: pd.DataFrame, price_df: pd.DataFrame, ads_df: pd.DataFrame | None = None) -> pd.DataFrame:
    sales = df[df["supplier_oper_name"] == "Продажа"]
    returns = df[df["supplier_oper_name"] == "Возврат"]

    if sales.empty:
        return pd.DataFrame()

    # Продажи
    agg = (
        sales.groupby(["nm_id", "sa_name"])
        .agg(qty=("quantity", "sum"), k_per=("ppvz_for_pay", "sum"))
        .reset_index()
    )

    # Возвраты
    if len(returns) > 0:
        ret = (
            returns.groupby(["nm_id", "sa_name"])
            .agg(qty=("quantity", "sum"), k_per=("ppvz_for_pay", "sum"))
            .reset_index()
        )
        ret["qty"] = -ret["qty"]
        ret["k_per"] = -ret["k_per"]
        agg = pd.concat([agg, ret])
        agg = (
            agg.groupby(["nm_id", "sa_name"])
            .agg(qty=("qty", "sum"), k_per=("k_per", "sum"))
            .reset_index()
        )

    # Логистика по nm_id
    log_rows = df[
        df["supplier_oper_name"].isin(["Логистика", "Коррекция логистики"])
        & (df["nm_id"] > 0)
    ]
    if len(log_rows) > 0:
        log_agg = log_rows.groupby("nm_id")["delivery_rub"].sum().reset_index()
        agg = agg.merge(log_agg, on="nm_id", how="left")
    if "delivery_rub" not in agg.columns:
        agg["delivery_rub"] = 0.0
    agg["delivery_rub"] = agg["delivery_rub"].fillna(0)

    # Стоимость лояльности
    lc = df[df["supplier_oper_name"] == "Стоимость участия в программе лояльности"]
    if len(lc) > 0:
        lc_agg = lc.groupby("nm_id")["cashback_commission_change"].sum().reset_index()
        agg = agg.merge(lc_agg, on="nm_id", how="left")
    if "cashback_commission_change" not in agg.columns:
        agg["cashback_commission_change"] = 0.0
    agg["cashback_commission_change"] = agg["cashback_commission_change"].fillna(0)

    # Баллы лояльности
    lb = df[
        df["supplier_oper_name"]
        == "Сумма удержанная за начисленные баллы программы лояльности"
    ]
    if len(lb) > 0:
        lb_agg = lb.groupby("nm_id")["cashback_amount"].sum().reset_index()
        agg = agg.merge(lb_agg, on="nm_id", how="left")
    if "cashback_amount" not in agg.columns:
        agg["cashback_amount"] = 0.0
    agg["cashback_amount"] = agg["cashback_amount"].fillna(0)

    # Расходы и выручка SKU
    agg["rashody"] = (
        agg["delivery_rub"]
        + agg["cashback_commission_change"]
        + agg["cashback_amount"]
    )
    agg["vyruchka"] = agg["k_per"] - agg["rashody"]

    # Себестоимость
    agg["sa_name"] = agg["sa_name"].astype(str).str.strip()
    agg = agg.merge(
        price_df[["Артикул", "Наименование", "Цена в рублях"]],
        left_on="sa_name",
        right_on="Артикул",
        how="left",
    )
    agg["sebes_total"] = (agg["qty"] * agg["Цена в рублях"]).round(2)

    # ДРР (рекламные расходы)
    if ads_df is not None and not ads_df.empty:
        agg = agg.merge(ads_df, on="nm_id", how="left")
    if "adv_sum" not in agg.columns:
        agg["adv_sum"] = 0.0
    agg["adv_sum"] = agg["adv_sum"].fillna(0)

    agg["profit"] = (agg["vyruchka"] - agg["sebes_total"]).round(2)
    agg["profit_with_drr"] = (agg["vyruchka"] - agg["sebes_total"] - agg["adv_sum"]).round(2)

    safe = agg["vyruchka"].replace(0, float("nan"))
    agg["margin"] = (agg["profit"] / safe * 100).round(1)
    agg["margin_with_drr"] = (agg["profit_with_drr"] / safe * 100).round(1)
    agg["drr_pct"] = (agg["adv_sum"] / safe * 100).round(1)

    agg = agg.sort_values("k_per", ascending=False).reset_index(drop=True)

    # Предупреждение о пропусках
    no_cost = agg[agg["Цена в рублях"].isna()]
    if len(no_cost) > 0:
        print(f"\n  SKU без себестоимости ({len(no_cost)} шт):")
        for _, r in no_cost.iterrows():
            print(
                f"    sa_name='{r['sa_name']}', nm_id={r['nm_id']}, "
                f"qty={r['qty']:.0f}, к_перечисл={r['k_per']:,.2f}"
            )

    return agg


# ── Печать юнит-экономики ────────────────────────────────────────────────

def print_unit_economics(agg: pd.DataFrame, label: str, summary: dict):
    if agg.empty:
        return

    print(f"\n{'=' * 70}")
    print(f"ЮНИТ-ЭКОНОМИКА: {label}")
    print(f"{'=' * 70}")

    header = (
        f"  {'Наименование':<40s} {'Шт':>4s} {'Выручка':>11s} {'Себес':>11s} "
        f"{'ДРР':>9s} {'Прибыль':>10s} {'Маржа':>6s} {'М-ДРР':>6s}"
    )
    print(header)
    print("  " + "-" * 100)

    for _, r in agg.iterrows():
        name = str(r.get("Наименование", r["sa_name"]))[:39]
        sebes = r["sebes_total"] if pd.notna(r["sebes_total"]) else 0
        profit_drr = r["profit_with_drr"] if pd.notna(r["profit_with_drr"]) else 0
        margin = f"{r['margin']:.1f}" if pd.notna(r["margin"]) else "??"
        margin_drr = f"{r['margin_with_drr']:.1f}" if pd.notna(r["margin_with_drr"]) else "??"
        print(
            f"  {name:<40s} {r['qty']:>4.0f} {r['vyruchka']:>11,.2f} {sebes:>11,.2f} "
            f"{r['adv_sum']:>9,.0f} {profit_drr:>10,.2f} {margin:>5s}% {margin_drr:>5s}%"
        )

    total_vyr = agg["vyruchka"].sum()
    total_sebes = agg["sebes_total"].sum()
    total_drr = agg["adv_sum"].sum()
    total_profit = total_vyr - total_sebes
    total_profit_drr = total_vyr - total_sebes - total_drr
    total_margin = total_profit / total_vyr * 100 if total_vyr else 0
    total_margin_drr = total_profit_drr / total_vyr * 100 if total_vyr else 0

    print("  " + "-" * 100)
    print(
        f"  {'ИТОГО':<40s} {agg['qty'].sum():>4.0f} {total_vyr:>11,.2f} {total_sebes:>11,.2f} "
        f"{total_drr:>9,.0f} {total_profit_drr:>10,.2f} {total_margin:>5.1f}% {total_margin_drr:>5.1f}%"
    )

    obshie = summary["storage"] + summary["deduction"]
    print(f"\n  Общие расходы (не по SKU):")
    print(f"    Хранение:   {summary['storage']:>10,.2f}")
    print(f"    Удержания:  {summary['deduction']:>10,.2f}")
    print(f"    ДРР итого:  {total_drr:>10,.2f}")
    acc_profit = total_profit - obshie
    acc_profit_drr = total_profit_drr - obshie
    margin_acc = acc_profit / total_vyr * 100 if total_vyr else 0
    margin_acc_drr = acc_profit_drr / total_vyr * 100 if total_vyr else 0
    print(f"\n  Прибыль аккаунта (без ДРР) = {acc_profit:,.2f}, маржа = {margin_acc:.1f}%")
    print(f"  Прибыль аккаунта (с ДРР)   = {acc_profit_drr:,.2f}, маржа = {margin_acc_drr:.1f}%")


# ── Сохранение Excel ────────────────────────────────────────────────────

def save_to_excel(agg: pd.DataFrame, filename: str):
    save_cols = [
        "Наименование", "sa_name", "nm_id", "qty",
        "k_per", "delivery_rub", "cashback_commission_change", "cashback_amount",
        "rashody", "vyruchka", "Цена в рублях", "sebes_total",
        "adv_sum", "drr_pct",
        "profit", "margin", "profit_with_drr", "margin_with_drr",
    ]
    rename = {
        "sa_name": "Артикул", "nm_id": "nmId", "qty": "Кол-во",
        "k_per": "К перечислению", "delivery_rub": "Логистика",
        "cashback_commission_change": "Стоим.лояльн.",
        "cashback_amount": "Баллы лояльн.",
        "rashody": "Расходы SKU", "vyruchka": "Выручка SKU",
        "Цена в рублях": "Себес 1шт", "sebes_total": "Себес итого",
        "adv_sum": "ДРР ₽", "drr_pct": "ДРР %",
        "profit": "Прибыль без ДРР", "margin": "Маржа без ДРР %",
        "profit_with_drr": "Прибыль с ДРР", "margin_with_drr": "Маржа с ДРР %",
    }
    cols = [c for c in save_cols if c in agg.columns]
    out = agg[cols].rename(columns=rename)
    out.to_excel(filename, index=False)
    print(f"Сохранено: {filename}")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("МАРЖИНАЛЬНОСТЬ WILDBERRIES")
    print("=" * 70)

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    month_start = yesterday.replace(day=1)

    DATE_YESTERDAY = yesterday.strftime("%Y-%m-%d")
    DATE_MONTH_START = month_start.strftime("%Y-%m-%d")

    print(f"Вчера: {yesterday.strftime('%d.%m.%Y')}")
    print(f"Месяц: с {month_start.strftime('%d.%m.%Y')}")

    # Прайс
    from config import PRICE_FILE
    price = load_price(PRICE_FILE)

    # Загрузка данных (один запрос за месяц, фильтруем вчера)
    print(f"\nЗагрузка отчёта за {DATE_MONTH_START} — {DATE_YESTERDAY}...")
    df_all = load_report(DATE_MONTH_START, DATE_YESTERDAY)
    df_all = prepare_df(df_all)
    print(f"Записей: {len(df_all)}")

    yesterday_date = yesterday.date()
    df_yesterday = df_all[df_all["sale_date"] == yesterday_date].copy()
    df_month = df_all.copy()

    print(f"Вчера: {len(df_yesterday)} записей")
    print(f"Месяц: {len(df_month)} записей")

    # ДРР (реклама) — date_to не может быть сегодня, используем позавчера для «вчера»
    day_before = (yesterday - timedelta(days=1)).strftime("%Y-%m-%d")
    ads_yesterday = get_wb_ads(day_before, day_before, "за вчера")
    ads_month = get_wb_ads(DATE_MONTH_START, day_before, "за месяц")

    # Вчера
    summary_y = calc_summary(df_yesterday, f"Вчера ({DATE_YESTERDAY})")
    agg_y = calc_unit_economics(df_yesterday, price, ads_yesterday)
    print_unit_economics(agg_y, f"Вчера ({DATE_YESTERDAY})", summary_y)

    # Месяц
    summary_m = calc_summary(df_month, f"С начала месяца ({DATE_MONTH_START} — {DATE_YESTERDAY})")
    agg_m = calc_unit_economics(df_month, price, ads_month)
    print_unit_economics(agg_m, f"С начала месяца ({DATE_MONTH_START} — {DATE_YESTERDAY})", summary_m)

    # Сохранение
    if not agg_y.empty:
        save_to_excel(agg_y, "wb_margin_yesterday.xlsx")
    if not agg_m.empty:
        save_to_excel(agg_m, "wb_margin_month.xlsx")

    print("\nГотово.")


def load_cached(period="month"):
    """Загрузить результаты из cache/. period: 'yesterday' или 'month'."""
    from data_loader import load as _ld
    s = "y" if period == "yesterday" else "m"
    return _ld(f"wb_df_{s}"), _ld(f"wb_sum_{s}"), _ld(f"wb_agg_{s}")


if __name__ == "__main__":
    main()
