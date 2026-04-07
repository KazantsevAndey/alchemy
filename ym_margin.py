"""
Маржинальность Яндекс Маркет.

Логика:
  - Выручка = цена карточки × кол-во (из отчёта Размещение, колонка "Ваша цена за шт.")
  - Затраты = гросс (до баллов/скидок) из отчёта по услугам
  - Субсидии НЕ участвуют в расчёте — они взаимно сокращаются с баллами
  - Маржа = (Выручка - Затраты гросс - Себестоимость) / Выручка

Вход:
  - Яндекс Маркет Partner API (отчёт united-marketplace-services)
  - Прайс (себестоимость) из локального Excel

Выход:
  - DataFrame с юнит-экономикой по SKU
  - dict с итогами по услугам
"""

import pandas as pd
import requests
import time
import io

from utils.price_loader import load_price, build_cost_map


YM_BASE = "https://api.partner.market.yandex.ru"


def _get_creds(creds):
    if creds is not None:
        return creds
    try:
        from config import YM_API_KEY, YM_CAMPAIGN_ID, YM_BUSINESS_ID
        return {
            "YM_API_KEY": YM_API_KEY,
            "YM_CAMPAIGN_ID": YM_CAMPAIGN_ID,
            "YM_BUSINESS_ID": YM_BUSINESS_ID,
        }
    except ImportError:
        return {}


def _ym_headers(creds):
    c = _get_creds(creds)
    return {
        "Api-Key": c["YM_API_KEY"],
        "Content-Type": "application/json",
    }

SVC_ORDER = [
    "Размещение", "Буст", "Доставка", "Хранение", "Перевод",
    "Приём", "Транзит", "Обработка", "Вывоз", "Утилизация",
]

# Главные услуги для отображения в колонках; остальные → "Прочее"
SVC_MAIN = ["Размещение", "Буст", "Доставка", "Хранение", "Перевод"]


# ── Генерация отчётов ────────────────────────────────────────────────

def _generate_report(endpoint: str, payload: dict, creds=None) -> bytes | None:
    url = f"{YM_BASE}/reports/{endpoint}/generate"
    headers = _ym_headers(creds)
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"  Ошибка генерации {endpoint}: {resp.status_code} — {resp.text[:200]}")
        return None

    report_id = resp.json()["result"]["reportId"]
    for _ in range(120):
        time.sleep(3)
        r = requests.get(
            f"{YM_BASE}/reports/info/{report_id}",
            headers=headers, timeout=30,
        )
        if r.status_code != 200:
            continue
        result = r.json().get("result", {})
        st = result.get("status")
        if st == "DONE":
            return requests.get(result["file"], timeout=60).content
        if st == "FAILED":
            print(f"  Отчёт FAILED: {r.json()}")
            return None

    print("  Таймаут генерации отчёта")
    return None


# ── Утилиты парсинга ─────────────────────────────────────────────────

def _find_col(headers, keywords, last=False, skip=None):
    """Найти индекс колонки по ключевым словам."""
    if not keywords:
        return None
    for kw in keywords:
        kw_lower = kw.lower()
        search = reversed(list(enumerate(headers))) if last else enumerate(headers)
        for i, h in search:
            if i == skip:
                continue
            if kw_lower in h:
                return i
    return None


def _find_header_and_sku(df_raw, max_rows=15):
    """Найти строку заголовка и колонку SKU."""
    for i in range(min(max_rows, len(df_raw))):
        for j in range(len(df_raw.columns)):
            cell = str(df_raw.iloc[i, j]).lower().strip()
            if cell in ("ваш sku", "артикул", "sku"):
                return i, j
    return None, None


# ── Парсинг отчёта по услугам ────────────────────────────────────────

def load_services_report(date_from: str, date_to: str, creds=None):
    """Загружает отчёт по услугам, парсит выручку (цена карточки) и затраты (гросс).

    Returns: (rev_df, costs_pivot, totals)
      rev_df:      DataFrame [sku, name, qty, revenue]
      costs_pivot: DataFrame [sku, Размещение, Буст, ...]
      totals:      dict {label: float}
    """
    c = _get_creds(creds)
    content = _generate_report("united-marketplace-services", {
        "businessId": c["YM_BUSINESS_ID"],
        "dateTimeFrom": f"{date_from}T00:00:00+03:00",
        "dateTimeTo": f"{date_to}T23:59:59+03:00",
    }, creds)
    if not content:
        empty_rev = pd.DataFrame(columns=["sku", "name", "qty", "revenue"])
        return empty_rev, pd.DataFrame(columns=["sku"]), {}

    xlsx = pd.ExcelFile(io.BytesIO(content))

    # ── Выручка из листа Размещение ──
    rev_rows = []
    razmesh = "Размещение товаров на витрине"
    if razmesh in xlsx.sheet_names:
        df_raw = pd.read_excel(xlsx, sheet_name=razmesh, header=None)
        header_row, sku_col = _find_header_and_sku(df_raw)
        if header_row is not None:
            headers = [str(df_raw.iloc[header_row, j]).lower().strip()
                       for j in range(len(df_raw.columns))]
            price_col = _find_col(headers, ["ваша цена за шт"])
            qty_col = _find_col(headers, ["количество, шт"])
            name_col = _find_col(headers, ["название товара"])

            if price_col is not None and qty_col is not None:
                for idx in range(header_row + 1, len(df_raw)):
                    sku_val = df_raw.iloc[idx, sku_col]
                    if pd.isna(sku_val):
                        continue
                    sku = str(sku_val).strip()
                    if not sku or sku == "nan":
                        continue

                    p = pd.to_numeric(df_raw.iloc[idx, price_col], errors="coerce")
                    q = pd.to_numeric(df_raw.iloc[idx, qty_col], errors="coerce")
                    if pd.isna(p): p = 0.0
                    if pd.isna(q): q = 0

                    nm = ""
                    if name_col is not None:
                        nm_val = df_raw.iloc[idx, name_col]
                        nm = str(nm_val).strip() if pd.notna(nm_val) else ""

                    rev_rows.append({"sku": sku, "name": nm, "qty": int(q), "revenue": p * q})

    if rev_rows:
        rev_df = pd.DataFrame(rev_rows).groupby("sku").agg({
            "name": "first", "qty": "sum", "revenue": "sum",
        }).reset_index()
    else:
        rev_df = pd.DataFrame(columns=["sku", "name", "qty", "revenue"])

    # ── Затраты (гросс) ──
    cost_configs = [
        ("Размещение товаров на витрине", "Размещение",
         ["стоимость услуги без скидок"], False),
        ("Буст продаж, оплата за продажи", "Буст",
         ["постоплата"], False),
        ("Доставка покупателю", "Доставка",
         ["стоимость услуги, ₽"], True),
        ("Платное хранение с 01.06.22", "Хранение",
         ["стоимость платного хранения", "стоимость услуги"], True),
        ("Перевод платежа", "Перевод",
         ["стоимость услуги, ₽"], True),
        ("Приём платежа", "Приём",
         ["стоимость услуги, ₽"], True),
        ("Поставка через транзитный склад", "Транзит",
         ["стоимость услуги"], True),
        ("Обработка заказов на складе", "Обработка",
         ["стоимость услуги"], True),
        ("Вывоз со склада, СЦ, ПВЗ", "Вывоз",
         ["стоимость услуги"], True),
        ("Организация утилизации", "Утилизация",
         ["стоимость услуги"], True),
    ]

    all_cost_rows = []
    totals = {}

    for sn, label, kw, cost_last in cost_configs:
        if sn not in xlsx.sheet_names:
            continue

        df_raw = pd.read_excel(xlsx, sheet_name=sn, header=None)
        header_row, sku_col = _find_header_and_sku(df_raw)

        if header_row is None:
            # Лист без SKU-разбивки (Транзит, Обработка и др.)
            alt_headers = [str(df_raw.iloc[1, j]).lower().strip()
                           for j in range(len(df_raw.columns))]
            cost_col = _find_col(alt_headers, kw, last=cost_last)
            if cost_col is None:
                cost_col = _find_col(alt_headers,
                    ["стоимость услуги", "стоимость"], last=True)
            if cost_col is not None:
                total = 0.0
                for idx in range(2, len(df_raw)):
                    v = pd.to_numeric(df_raw.iloc[idx, cost_col], errors="coerce")
                    if pd.notna(v):
                        total += v
                totals[label] = total
            continue

        headers = [str(df_raw.iloc[header_row, j]).lower().strip()
                   for j in range(len(df_raw.columns))]
        cost_col = _find_col(headers, kw, last=cost_last)
        if cost_col is None:
            continue

        total = 0.0
        for idx in range(header_row + 1, len(df_raw)):
            sku_val = df_raw.iloc[idx, sku_col]
            if pd.isna(sku_val):
                continue
            sku = str(sku_val).strip()
            if not sku or sku == "nan":
                continue

            v = pd.to_numeric(df_raw.iloc[idx, cost_col], errors="coerce")
            if pd.isna(v):
                v = 0.0
            total += v
            all_cost_rows.append({"sku": sku, "service": label, "cost": v})

        totals[label] = total

    if all_cost_rows:
        costs_df = pd.DataFrame(all_cost_rows)
        costs_pivot = costs_df.pivot_table(
            index="sku", columns="service", values="cost",
            aggfunc="sum", fill_value=0,
        ).reset_index()
        costs_pivot.columns.name = None
    else:
        costs_pivot = pd.DataFrame(columns=["sku"])

    print(f"  Выручка: {len(rev_df)} SKU, {rev_df['qty'].sum():,.0f} шт, "
          f"{rev_df['revenue'].sum():,.0f} ₽")
    print(f"  Затраты гросс: {sum(totals.values()):,.0f} ₽")

    return rev_df, costs_pivot, totals


# ── Расчёт маржи ────────────────────────────────────────────────────

def calc_margin(rev_df, costs_pivot, totals, cost_map, period_name=""):
    """Рассчитать юнит-экономику по SKU.

    Returns: DataFrame с колонками: sku, name, qty, revenue,
             Размещение..Перевод, Прочее, Затраты, sebes_unit, sebes_total,
             Прибыль, Маржа_pct
    """
    if rev_df.empty:
        return pd.DataFrame()

    R = rev_df.copy()

    # Добавляем затраты по SKU
    if not costs_pivot.empty:
        R = R.merge(costs_pivot, on="sku", how="left")

    # SKU только с затратами (без продаж) — например хранение
    if not costs_pivot.empty and "sku" in costs_pivot.columns:
        storage_skus = set(costs_pivot["sku"]) - set(R["sku"])
        for ms in storage_skus:
            row_data = costs_pivot[costs_pivot["sku"] == ms].iloc[0]
            new_row = {"sku": ms, "name": "(только хранение)", "qty": 0, "revenue": 0}
            for col in costs_pivot.columns:
                if col != "sku":
                    new_row[col] = row_data.get(col, 0)
            R = pd.concat([R, pd.DataFrame([new_row])], ignore_index=True)

    R = R.fillna(0)

    # Прочее = сумма услуг не в SVC_MAIN
    other_svcs = [s for s in SVC_ORDER if s not in SVC_MAIN and s in R.columns]
    R["Прочее"] = R[other_svcs].sum(axis=1) if other_svcs else 0

    # Затраты итого
    cost_cols = [s for s in SVC_MAIN if s in R.columns] + ["Прочее"]
    R["Затраты"] = R[cost_cols].sum(axis=1)

    # Себестоимость
    R["sebes_unit"] = R["sku"].map(cost_map).fillna(0)
    R["sebes_total"] = R["sebes_unit"] * R["qty"]

    # Прибыль и маржа
    R["Прибыль"] = R["revenue"] - R["Затраты"] - R["sebes_total"]
    R["Маржа_pct"] = (R["Прибыль"] / R["revenue"].replace(0, 0.001) * 100).round(1)
    R.loc[R["revenue"] == 0, "Маржа_pct"] = 0

    R = R.sort_values("revenue", ascending=False).reset_index(drop=True)

    # Печать сводки
    total_rev = R["revenue"].sum()
    total_costs = sum(totals.values())
    total_sebes = R["sebes_total"].sum()
    profit = total_rev - total_costs - total_sebes
    margin = (profit / total_rev * 100) if total_rev else 0

    print(f"\n{'=' * 50}")
    print(f"{period_name}")
    print(f"{'=' * 50}")
    print(f"Выручка (карточка):    {total_rev:>12,.0f} ₽")
    for svc in SVC_ORDER:
        v = totals.get(svc, 0)
        if v > 0:
            print(f"  {svc:20s}  {v:>12,.0f}")
    print(f"  {'ИТОГО затраты':20s}  {total_costs:>12,.0f}")
    print(f"Себестоимость:         {total_sebes:>12,.0f}")
    print(f"Прибыль:               {profit:>12,.0f}")
    print(f"Маржа:                 {margin:>11.1f}%")

    return R


# ── Сохранение Excel ────────────────────────────────────────────────

def save_excel(R, totals, filename):
    if R.empty:
        print(f"Нет данных для {filename}")
        return

    out_cols = ["sku", "name", "qty", "revenue"]
    out_cols += [s for s in SVC_MAIN if s in R.columns]
    out_cols += ["Прочее", "Затраты", "sebes_unit", "sebes_total", "Прибыль", "Маржа_pct"]

    out = R[[c for c in out_cols if c in R.columns]].copy()

    rename = {
        "sku": "Артикул", "name": "Название", "qty": "Шт",
        "revenue": "Выручка", "Затраты": "Затраты итого",
        "sebes_unit": "Себест/шт", "sebes_total": "Себестоимость",
        "Маржа_pct": "Маржа %",
    }
    out = out.rename(columns=rename)

    out.to_excel(filename, index=False, sheet_name="По SKU")
    print(f"Сохранено: {filename}")


# ── main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("МАРЖИНАЛЬНОСТЬ ЯНДЕКС МАРКЕТ")
    print("=" * 60)

    from datetime import datetime, timedelta
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    month_start = yesterday.replace(day=1)

    dy = yesterday.strftime("%Y-%m-%d")
    dm = month_start.strftime("%Y-%m-%d")

    print(f"Вчера: {yesterday.strftime('%d.%m.%Y')}")
    print(f"Месяц: с {month_start.strftime('%d.%m.%Y')}")

    from config import PRICE_FILE
    cost_map = build_cost_map(load_price(PRICE_FILE))

    # Месяц
    print(f"\nЗагружаю данные за месяц ({dm} — {dy})...")
    rev_m, costs_m, totals_m = load_services_report(dm, dy)
    R_m = calc_margin(rev_m, costs_m, totals_m, cost_map, f"МЕСЯЦ ({dm} — {dy})")

    # Вчера
    print(f"\nЗагружаю данные за вчера ({dy})...")
    rev_y, costs_y, totals_y = load_services_report(dy, dy)
    R_y = calc_margin(rev_y, costs_y, totals_y, cost_map, f"ВЧЕРА ({dy})")

    save_excel(R_m, totals_m, "ym_margin_month.xlsx")
    save_excel(R_y, totals_y, "ym_margin_yesterday.xlsx")

    print("\nГотово.")


if __name__ == "__main__":
    main()
