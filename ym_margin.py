"""
Маржинальность Яндекс Маркет — автономный скрипт.

Логика расчёта (совпадает с закрывающими документами ЯМ):
  - Выручка = цена С УЧЁТОМ скидок (что в чеке покупателя)
  - Услуги ЯМ = валовые из отчёта по услугам (для детализации по SKU)
  - Баллы Маркета = компенсация скидок, снижают услуги ЯМ
  - Премия = излишек баллов сверх 99% услуг, выплачивается кэшем
  - Услуги по акту = валовые услуги - баллы (= фактическое удержание)
  - Маржа = выручка + премия - услуги_по_акту - себестоимость

Вход:
  - Прайс (себестоимость) из локального Excel
  - Яндекс Маркет Partner API (заказы + отчёт по услугам + отчёт о платежах)

Выход:
  - ym_margin_month.xlsx   (листы: Сводка, По SKU)
  - ym_margin_yesterday.xlsx
"""

import pandas as pd
import requests
import time
import io
from datetime import datetime, timedelta

from config import YM_API_KEY, YM_CAMPAIGN_ID, YM_BUSINESS_ID, PRICE_FILE
from utils.price_loader import load_price, build_cost_map


YM_HEADERS = {
    "Api-Key": YM_API_KEY,
    "Content-Type": "application/json",
}

SVC_ORDER = [
    "Размещение", "Буст продаж", "Доставка", "Приём платежа",
    "Перевод платежа", "Хранение", "Транзит", "Обработка",
]


# ── Генерация отчётов ────────────────────────────────────────────────

def _generate_report(endpoint: str, payload: dict, fmt: str = "FILE") -> bytes | None:
    url = f"https://api.partner.market.yandex.ru/reports/{endpoint}/generate"
    resp = requests.post(url, headers=YM_HEADERS, json=payload, params={"format": fmt})
    if resp.status_code != 200:
        print(f"  Ошибка генерации {endpoint}: {resp.status_code} — {resp.text[:200]}")
        return None

    report_id = resp.json()["result"]["reportId"]
    for _ in range(120):
        time.sleep(3)
        r = requests.get(
            f"https://api.partner.market.yandex.ru/reports/info/{report_id}",
            headers=YM_HEADERS,
        )
        if r.status_code != 200:
            continue
        result = r.json().get("result", {})
        st = result.get("status")
        if st == "DONE":
            return requests.get(result["file"]).content
        if st == "FAILED":
            print(f"  Отчёт FAILED: {r.json()}")
            return None

    print("  Таймаут генерации отчёта")
    return None


# ── Отчёт по услугам (валовый, для детализации по SKU) ───────────────

def load_services(date_from: str, date_to: str):
    """Загружает и парсит отчёт по стоимости услуг (валовый).

    Returns: (costs_pivot, totals, grand_total)
    """
    content = _generate_report("united-marketplace-services", {
        "businessId": YM_BUSINESS_ID,
        "dateTimeFrom": f"{date_from}T00:00:00+03:00",
        "dateTimeTo": f"{date_to}T23:59:59+03:00",
    })
    if not content:
        return None, {}, 0

    xlsx = pd.ExcelFile(io.BytesIO(content))

    cfg = [
        ("Размещение товаров на витрине", 6, 9, 34, "Размещение"),
        ("Буст продаж, оплата за продажи", 2, 8, 16, "Буст продаж"),
        ("Доставка покупателю",            2, 8, 35, "Доставка"),
        ("Приём платежа",                  5, 8, 15, "Приём платежа"),
        ("Перевод платежа",                2, 8, 15, "Перевод платежа"),
        ("Платное хранение с 01.06.22",    6, 8, 25, "Хранение"),
        ("Поставка через транзитный склад", 2, 8, 20, "Транзит"),
        ("Обработка заказов на складе",    2, 8, 13, "Обработка"),
    ]

    all_costs = []
    totals = {}
    for sn, start, sc, cc, label in cfg:
        if sn not in xlsx.sheet_names:
            continue
        df = pd.read_excel(xlsx, sheet_name=sn, header=None)
        total = 0.0
        for idx in range(start, len(df)):
            sku = df.iloc[idx, sc]
            if pd.isna(sku):
                continue
            v = pd.to_numeric(df.iloc[idx, cc], errors="coerce")
            if pd.isna(v):
                v = 0.0
            total += v
            all_costs.append({"sku": str(sku).strip(), "service": label, "cost": v})
        totals[label] = total

    costs_df = pd.DataFrame(all_costs)
    if len(costs_df) == 0:
        return pd.DataFrame(columns=["sku"]), totals, 0

    costs_pivot = (
        costs_df
        .pivot_table(index="sku", columns="service", values="cost", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    costs_pivot.columns.name = None
    return costs_pivot, totals, sum(totals.values())


# ── Отчёт о платежах (netting) — акт + премия ───────────────────────

def load_netting(date_from: str, date_to: str) -> dict:
    """Загружает отчёт о платежах и извлекает:
    - act_amount: сумма удержания за услуги по акту ЗА ПЕРИОД (со знаком +)
    - premium: сумма премии (баллы → кэш)
    - refunds: сумма возвратов покупателям

    Фильтрует акты по дате: берёт только акты, дата которых попадает
    в запрошенный период (чтобы не захватить акт за прошлый месяц).

    Returns: dict с ключами act_amount, premium, refunds
    """
    content = _generate_report("united-netting", {
        "businessId": YM_BUSINESS_ID,
        "dateFrom": date_from,
        "dateTo": date_to,
    })
    result = {"act_amount": 0, "premium": 0, "refunds": 0}
    if not content:
        return result

    xlsx = pd.ExcelFile(io.BytesIO(content))
    if "Отчёт о платежах" not in xlsx.sheet_names:
        return result

    df = pd.read_excel(xlsx, sheet_name="Отчёт о платежах", header=1)
    df["Сумма транзакции, ₽"] = pd.to_numeric(
        df["Сумма транзакции, ₽"], errors="coerce"
    ).fillna(0)

    tx = df["Сумма транзакции, ₽"]
    typ = df["Тип транзакции"]
    src = df["Источник транзакции"]

    # Удержание за услуги — фильтруем по дате акта
    act_col = "Дата акта об оказанных услугах"
    udr_mask = (typ == "Удержание") & (src == "Оплата услуг Яндекс.Маркета")

    if act_col in df.columns:
        act_dates = pd.to_datetime(
            df[act_col], format="%d.%m.%Y", errors="coerce"
        )
        dt_from = pd.Timestamp(date_from)
        dt_to = pd.Timestamp(date_to)
        period_mask = (act_dates >= dt_from) & (act_dates <= dt_to)
        act = -tx[udr_mask & period_mask].sum()
    else:
        act = -tx[udr_mask].sum()

    # Премия
    premium = tx[
        (typ == "Начисление") & (src == "Премия")
    ].sum()

    # Возвраты
    refunds = -tx[
        (typ == "Возврат") & (src == "Возврат платежа покупателя")
    ].sum()

    result["act_amount"] = act
    result["premium"] = premium
    result["refunds"] = refunds
    return result


# ── Заказы ───────────────────────────────────────────────────────────

def load_orders(date_from: str, date_to: str) -> pd.DataFrame:
    all_orders = []
    page = 1
    while True:
        resp = requests.get(
            f"https://api.partner.market.yandex.ru/campaigns/{YM_CAMPAIGN_ID}/orders",
            headers=YM_HEADERS,
            params={
                "status": "DELIVERED",
                "fromDate": date_from,
                "toDate": date_to,
                "pageSize": 50,
                "page": page,
            },
        )
        if resp.status_code != 200:
            print(f"  Ошибка заказов: {resp.status_code}")
            break
        data = resp.json()
        orders = data.get("orders", [])
        if not orders:
            break
        all_orders.extend(orders)
        if page >= data.get("pager", {}).get("pagesCount", 1):
            break
        page += 1

    rows = []
    for order in all_orders:
        for item in order.get("items", []):
            cnt = item.get("count", 1)
            buyer_price = item.get("price", 0)
            subs = item.get("subsidies") or []
            subsidy = sum(s.get("amount", 0) for s in subs)
            my_price = buyer_price + subsidy
            rows.append({
                "sku": str(item.get("offerId", "")).strip(),
                "name": item.get("offerName", ""),
                "qty": cnt,
                "rev_full": my_price * cnt,
                "buyer_paid": buyer_price * cnt,
                "subsidy": subsidy * cnt,
            })

    if not rows:
        return pd.DataFrame(columns=["sku", "name", "qty", "rev_full", "buyer_paid", "subsidy"])

    df = pd.DataFrame(rows)
    return (
        df.groupby("sku")
        .agg({
            "name": "first", "qty": "sum",
            "rev_full": "sum", "buyer_paid": "sum", "subsidy": "sum",
        })
        .reset_index()
    )


# ── Расчёт маржи ────────────────────────────────────────────────────

def calc_margin(
    sales_agg: pd.DataFrame,
    costs_pivot,
    totals: dict,
    cost_map: dict,
    netting: dict,
    period_name: str,
) -> pd.DataFrame:
    R = sales_agg.copy()

    for svc in SVC_ORDER:
        if costs_pivot is not None and svc in costs_pivot.columns:
            R = R.merge(costs_pivot[["sku", svc]], on="sku", how="left")
        else:
            R[svc] = 0

    # SKU только с хранением (без продаж)
    if costs_pivot is not None and len(costs_pivot) > 0:
        storage_skus = set(costs_pivot["sku"]) - set(R["sku"])
        for ms in storage_skus:
            row_data = costs_pivot[costs_pivot["sku"] == ms].iloc[0]
            new_row = {
                "sku": ms, "name": "(только хранение)", "qty": 0,
                "rev_full": 0, "buyer_paid": 0, "subsidy": 0,
            }
            for svc in SVC_ORDER:
                new_row[svc] = row_data.get(svc, 0)
            R = pd.concat([R, pd.DataFrame([new_row])], ignore_index=True)

    R = R.fillna(0)
    R["Себес_шт"] = R["sku"].map(cost_map).fillna(0)
    R["Себес_итого"] = R["Себес_шт"] * R["qty"]
    R["Услуги_валовые"] = R[SVC_ORDER].sum(axis=1)
    R["Услуги_валовые_без_ДРР"] = R[[s for s in SVC_ORDER if s != "Буст продаж"]].sum(axis=1)

    # ── Баллы и акт ──
    gross_svc = sum(totals.values())
    act_amount = netting.get("act_amount", 0)
    premium = netting.get("premium", 0)

    # Если акт есть (закрытый период) — считаем по документам.
    # Если акта нет (текущий месяц) — считаем методом Б:
    #   прибыль ≈ полная_цена − валовые_услуги − себестоимость
    #   (субсидии и баллы взаимно сокращаются)
    has_act = act_amount > 0

    if has_act:
        bally = gross_svc - act_amount
        act_ratio = act_amount / gross_svc if gross_svc > 0 else 1.0
        R["Услуги_по_акту"] = R["Услуги_валовые"] * act_ratio
        R["Услуги_по_акту_без_ДРР"] = R["Услуги_валовые_без_ДРР"] * act_ratio
        total_buyer_paid = R["buyer_paid"].sum()
        if total_buyer_paid > 0:
            R["Премия"] = R["buyer_paid"] / total_buyer_paid * premium
        else:
            R["Премия"] = 0
        # Маржа: выручка(чек) + премия − услуги_по_акту − себестоимость
        R["После_комиссий"] = R["buyer_paid"] + R["Премия"] - R["Услуги_по_акту"]
        R["После_комиссий_без_ДРР"] = R["buyer_paid"] + R["Премия"] - R["Услуги_по_акту_без_ДРР"]
    else:
        bally = 0
        R["Услуги_по_акту"] = R["Услуги_валовые"]
        R["Услуги_по_акту_без_ДРР"] = R["Услуги_валовые_без_ДРР"]
        R["Премия"] = 0
        # Метод Б: полная цена − валовые услуги (субсидии ≈ баллы, сокращаются)
        R["После_комиссий"] = R["rev_full"] - R["Услуги_валовые"]
        R["После_комиссий_без_ДРР"] = R["rev_full"] - R["Услуги_валовые_без_ДРР"]

    R["Маржа_с_ДРР"] = R["После_комиссий"] - R["Себес_итого"]
    R["Маржа_без_ДРР"] = R["После_комиссий_без_ДРР"] - R["Себес_итого"]

    # Маржа % считаем от rev_full (единая база для сравнения периодов)
    R["%_маржи_с_ДРР"] = R.apply(
        lambda r: r["Маржа_с_ДРР"] / r["rev_full"] if r["rev_full"] > 0 else 0,
        axis=1,
    )
    R["%_маржи_без_ДРР"] = R.apply(
        lambda r: r["Маржа_без_ДРР"] / r["rev_full"] if r["rev_full"] > 0 else 0,
        axis=1,
    )
    R = R.sort_values("rev_full", ascending=False).reset_index(drop=True)

    # ── Сводка ──
    total_rev = R["buyer_paid"].sum()
    total_rev_full = R["rev_full"].sum()
    total_subsidy = R["subsidy"].sum()
    total_sebes = R["Себес_итого"].sum()

    if has_act:
        after_comm = total_rev + premium - act_amount
        gross_no_drr = sum(v for k, v in totals.items() if k != "Буст продаж")
        act_no_drr = act_amount * (gross_no_drr / gross_svc if gross_svc > 0 else 1.0)
        after_comm_no_drr = total_rev + premium - act_no_drr
    else:
        after_comm = total_rev_full - gross_svc
        gross_no_drr = sum(v for k, v in totals.items() if k != "Буст продаж")
        after_comm_no_drr = total_rev_full - gross_no_drr

    margin = after_comm - total_sebes
    margin_no_drr = after_comm_no_drr - total_sebes

    method = "по акту" if has_act else "оценка (метод Б)"

    print(f"\n{'=' * 60}")
    print(f"{period_name}  [{method}]")
    print(f"{'=' * 60}")
    print(f"Выручка (полная цена):     {total_rev_full:>12,.0f}")
    print(f"  покупатель заплатил:     {total_rev:>12,.0f}")
    print(f"  субсидии ЯМ:             {total_subsidy:>12,.0f}  (виртуальные)")
    print()
    print(f"Услуги ЯМ (валовые):")
    for svc in SVC_ORDER:
        print(f"  {svc:27s}  {totals.get(svc, 0):>12,.0f}")
    print(f"  {'─' * 42}")
    print(f"  {'ИТОГО валовые':27s}  {gross_svc:>12,.0f}")
    if has_act:
        print()
        print(f"Баллы Маркета (зачёт):     {bally:>12,.0f}")
        print(f"Услуги по акту:            {act_amount:>12,.0f}  = валовые − баллы")
        print(f"Премия (кэш):            +{premium:>12,.0f}")
    print()
    print(f"После комиссий (с ДРР):    {after_comm:>12,.0f}")
    print(f"Себестоимость:             {total_sebes:>12,.0f}")
    print()
    base = total_rev if has_act else total_rev_full
    base_label = "выручки(чек)" if has_act else "полной цены"
    if base > 0:
        print(f"МАРЖА с ДРР:               {margin:>12,.0f}  ({margin / base * 100:.1f}% от {base_label})")
    else:
        print("МАРЖА с ДРР: 0")
    if base > 0:
        print(f"МАРЖА без ДРР:             {margin_no_drr:>12,.0f}  ({margin_no_drr / base * 100:.1f}% от {base_label})")
    else:
        print("МАРЖА без ДРР: 0")

    return R


# ── Сохранение Excel ────────────────────────────────────────────────

def save_excel(R: pd.DataFrame, totals: dict, netting: dict, filename: str):
    display_cols = [
        "sku", "name", "qty", "buyer_paid", "rev_full", "subsidy",
        "Себес_шт", "Себес_итого",
    ] + SVC_ORDER + [
        "Услуги_валовые", "Услуги_по_акту", "Премия",
        "Маржа_без_ДРР", "%_маржи_без_ДРР",
        "Маржа_с_ДРР", "%_маржи_с_ДРР",
    ]
    col_names = [
        "SKU", "Название", "Шт", "Выручка (чек)", "Полная цена", "Субсидии ЯМ",
        "Себес/шт", "Себес итого",
    ] + SVC_ORDER + [
        "Услуги валовые", "Услуги по акту", "Премия",
        "Маржа без ДРР ₽", "Маржа без ДРР %",
        "Маржа с ДРР ₽", "Маржа с ДРР %",
    ]

    out = R[[c for c in display_cols if c in R.columns]].copy()
    out.columns = col_names[: len(out.columns)]
    if "Маржа без ДРР %" in out.columns:
        out["Маржа без ДРР %"] = (out["Маржа без ДРР %"] * 100).round(1)
    if "Маржа с ДРР %" in out.columns:
        out["Маржа с ДРР %"] = (out["Маржа с ДРР %"] * 100).round(1)

    gross_svc = sum(totals.values())
    act_amount = netting.get("act_amount", 0)
    premium = netting.get("premium", 0)
    has_act = act_amount > 0
    bally = gross_svc - act_amount if has_act else 0

    total_rev = R["buyer_paid"].sum()
    total_rev_full = R["rev_full"].sum()
    total_subsidy = R["subsidy"].sum()
    total_sebes = R["Себес_итого"].sum()

    if has_act:
        after_comm = total_rev + premium - act_amount
        gross_no_drr = sum(v for k, v in totals.items() if k != "Буст продаж")
        act_no_drr = act_amount * (gross_no_drr / gross_svc if gross_svc > 0 else 1.0)
        after_comm_no_drr = total_rev + premium - act_no_drr
        base = total_rev
    else:
        after_comm = total_rev_full - gross_svc
        gross_no_drr = sum(v for k, v in totals.items() if k != "Буст продаж")
        after_comm_no_drr = total_rev_full - gross_no_drr
        base = total_rev_full

    method = "по акту" if has_act else "оценка (метод Б)"

    rows_summary = [
        ("Метод расчёта", method),
        ("Выручка (полная цена)", total_rev_full),
        ("  покупатель заплатил", total_rev),
        ("  субсидии ЯМ (виртуальные)", total_subsidy),
        ("", ""),
    ]
    for s in SVC_ORDER:
        rows_summary.append((f"  {s}", totals.get(s, 0)))
    rows_summary += [
        ("ИТОГО услуги (валовые)", gross_svc),
        ("", ""),
    ]
    if has_act:
        rows_summary += [
            ("Баллы Маркета (зачёт)", bally),
            ("Услуги по акту (валовые − баллы)", act_amount),
            ("Премия (кэш на р/с)", premium),
            ("", ""),
        ]
    rows_summary += [
        ("После комиссий (с ДРР)", after_comm),
        ("После комиссий (без ДРР)", after_comm_no_drr),
        ("Себестоимость", total_sebes),
        ("", ""),
        ("МАРЖА с ДРР", after_comm - total_sebes),
        ("% маржи с ДРР", round((after_comm - total_sebes) / base * 100, 1) if base else 0),
        ("МАРЖА без ДРР", after_comm_no_drr - total_sebes),
        ("% маржи без ДРР", round((after_comm_no_drr - total_sebes) / base * 100, 1) if base else 0),
    ]

    summary = pd.DataFrame(rows_summary, columns=["Статья", "Сумма"])

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Сводка", index=False)
        out.to_excel(writer, sheet_name="По SKU", index=False)

    print(f"Сохранено: {filename}")


# ── main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("МАРЖИНАЛЬНОСТЬ ЯНДЕКС МАРКЕТ")
    print("=" * 60)

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    month_start = yesterday.replace(day=1)

    DATE_YESTERDAY = yesterday.strftime("%Y-%m-%d")
    DATE_MONTH_START = month_start.strftime("%Y-%m-%d")

    print(f"Вчера: {yesterday.strftime('%d.%m.%Y')}")
    print(f"Месяц: с {month_start.strftime('%d.%m.%Y')}")

    # Прайс
    price = load_price(PRICE_FILE)
    cost_map = build_cost_map(price)

    # === Месяц ===
    print(f"\nЗагружаю услуги за месяц ({DATE_MONTH_START} — {DATE_YESTERDAY})...")
    costs_pivot_m, totals_m, grand_m = load_services(DATE_MONTH_START, DATE_YESTERDAY)
    print(f"Услуги (валовые): {grand_m:,.0f}")

    print("Загружаю платежи за месяц...")
    netting_m = load_netting(DATE_MONTH_START, DATE_YESTERDAY)
    print(f"Акт: {netting_m['act_amount']:,.0f}  Премия: {netting_m['premium']:,.0f}")

    print("Загружаю заказы за месяц...")
    sales_m = load_orders(DATE_MONTH_START, DATE_YESTERDAY)
    print(f"Заказов: {len(sales_m)} SKU, выручка (чек) {sales_m['buyer_paid'].sum():,.0f}")

    R_m = calc_margin(
        sales_m, costs_pivot_m, totals_m, cost_map, netting_m,
        f"ITALCO FBY — Месяц ({DATE_MONTH_START} — {DATE_YESTERDAY})",
    )

    # === Вчера ===
    print(f"\nЗагружаю услуги за вчера ({DATE_YESTERDAY})...")
    costs_pivot_y, totals_y, grand_y = load_services(DATE_YESTERDAY, DATE_YESTERDAY)
    print(f"Услуги (валовые): {grand_y:,.0f}")

    print("Загружаю платежи за вчера...")
    netting_y = load_netting(DATE_YESTERDAY, DATE_YESTERDAY)
    print(f"Акт: {netting_y['act_amount']:,.0f}  Премия: {netting_y['premium']:,.0f}")

    print("Загружаю заказы за вчера...")
    sales_y = load_orders(DATE_YESTERDAY, DATE_YESTERDAY)
    print(f"Заказов: {len(sales_y)} SKU, выручка (чек) {sales_y['buyer_paid'].sum():,.0f}")

    R_y = calc_margin(
        sales_y, costs_pivot_y, totals_y, cost_map, netting_y,
        f"ITALCO FBY — Вчера ({DATE_YESTERDAY})",
    )

    # Сохранение
    save_excel(R_m, totals_m, netting_m, "ym_margin_month.xlsx")
    save_excel(R_y, totals_y, netting_y, "ym_margin_yesterday.xlsx")

    print("\nГотово.")


if __name__ == "__main__":
    main()
