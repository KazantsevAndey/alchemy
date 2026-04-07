"""
Проверочный скрипт: данные ЯМ за март 2026.
Логика: выручка = цена карточки, затраты = гросс (до баллов), без субсидий.
"""

import pandas as pd
import requests
import time
import io
from config import PRICE_FILE
from utils.price_loader import load_price, build_cost_map

# ── Параметры ─────────────────────────────────────────────────────────────

YM_TOKEN = "ACMA:O7lnJJUOJitXrBdqfH5c5rR6ydoUBumfdEMKY4rY:fe65aba6"
YM_CAMPAIGN_ID = 21526273
YM_BUSINESS_ID = 714498
YM_HEADERS = {"Api-Key": YM_TOKEN, "Content-Type": "application/json"}
YM_BASE = "https://api.partner.market.yandex.ru"
DATE_FROM = "2026-03-01"
DATE_TO = "2026-03-31"


# ── Генерация и скачивание отчёта ─────────────────────────────────────────

def generate_and_download_report():
    print("  Генерация отчёта по услугам...")
    resp = requests.post(
        f"{YM_BASE}/reports/united-marketplace-services/generate",
        headers=YM_HEADERS,
        json={
            "businessId": YM_BUSINESS_ID,
            "dateTimeFrom": f"{DATE_FROM}T00:00:00+03:00",
            "dateTimeTo": f"{DATE_TO}T23:59:59+03:00",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  Ошибка генерации: {resp.status_code} — {resp.text[:300]}")
        return None

    report_id = resp.json()["result"]["reportId"]
    print(f"  Report ID: {report_id}")

    for attempt in range(120):
        time.sleep(3)
        r = requests.get(
            f"{YM_BASE}/reports/info/{report_id}",
            headers=YM_HEADERS, timeout=30,
        )
        if r.status_code != 200:
            continue
        result = r.json().get("result", {})
        st = result.get("status")
        if st == "DONE":
            print(f"  Отчёт готов (попытка {attempt + 1})")
            return requests.get(result["file"], timeout=60).content
        if st == "FAILED":
            print(f"  Отчёт FAILED: {r.json()}")
            return None

    print("  Таймаут ожидания")
    return None


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


# ── Парсинг отчёта ────────────────────────────────────────────────────────

def parse_report(content: bytes):
    """Парсит XLSX. Возвращает (revenue_by_sku, costs_by_sku, totals).

    revenue_by_sku: DataFrame [sku, name, qty, revenue]  — из листа Размещение
    costs_by_sku:   DataFrame [sku, Размещение, Буст, Доставка, ...]  — гросс
    totals:         dict {label: float}  — итоги по каждой услуге (гросс)
    """
    xlsx = pd.ExcelFile(io.BytesIO(content))
    print(f"  Листы: {xlsx.sheet_names}")

    # ── 1. Выручка из листа Размещение ──
    # "Ваша цена за шт." × "Количество, шт."
    rev_rows = []
    sheet_name = "Размещение товаров на витрине"
    if sheet_name in xlsx.sheet_names:
        df_raw = pd.read_excel(xlsx, sheet_name=sheet_name, header=None)
        header_row, sku_col = _find_header_and_sku(df_raw)
        if header_row is not None:
            headers = [str(df_raw.iloc[header_row, j]).lower().strip()
                       for j in range(len(df_raw.columns))]

            price_col = _find_col(headers, ["ваша цена за шт"])
            qty_col = _find_col(headers, ["количество, шт"])
            name_col = _find_col(headers, ["название товара"])

            print(f"  [Выручка] Размещение: header={header_row}, "
                  f"sku={sku_col}, price={price_col}, qty={qty_col}, name={name_col}")

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

                    rev_rows.append({
                        "sku": sku,
                        "name": nm,
                        "qty": int(q),
                        "revenue": p * q,
                    })

    if rev_rows:
        rev_df = pd.DataFrame(rev_rows).groupby("sku").agg({
            "name": "first", "qty": "sum", "revenue": "sum",
        }).reset_index()
        print(f"  Выручка: {len(rev_df)} SKU, {rev_df['qty'].sum():,.0f} шт, "
              f"{rev_df['revenue'].sum():,.0f} ₽")
    else:
        rev_df = pd.DataFrame(columns=["sku", "name", "qty", "revenue"])
        print("  Выручка: нет данных из Размещения!")

    # ── 2. Затраты (гросс) ──
    # Конфиг: (sheet_name, label, cost_keywords, cost_last)
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
            # Лист без SKU (Транзит, Обработка и др.)
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
                print(f"  [{label}] {total:,.0f} ₽ (без SKU-разбивки)")
            else:
                print(f"  [{label}] колонка стоимости не найдена")
            continue

        headers = [str(df_raw.iloc[header_row, j]).lower().strip()
                   for j in range(len(df_raw.columns))]

        cost_col = _find_col(headers, kw, last=cost_last)
        if cost_col is None:
            print(f"  [{label}] колонка гросс не найдена. Колонки: {headers}")
            continue

        print(f"  [{label}] col {cost_col}: {headers[cost_col]}")

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
        print(f"  [{label}] ИТОГО: {total:,.0f} ₽")

    # Pivot costs by SKU
    if all_cost_rows:
        costs_df = pd.DataFrame(all_cost_rows)
        costs_pivot = costs_df.pivot_table(
            index="sku", columns="service", values="cost",
            aggfunc="sum", fill_value=0,
        ).reset_index()
        costs_pivot.columns.name = None
    else:
        costs_pivot = pd.DataFrame(columns=["sku"])

    return rev_df, costs_pivot, totals


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("ПРОВЕРКА ЯМ — МАРТ 2026 (гросс, без субсидий)")
    print("=" * 70)

    # Отчёт по услугам
    print("\n=== ШАГ 1: ОТЧЁТ ПО УСЛУГАМ ===")
    content = generate_and_download_report()
    if content is None:
        print("Не удалось получить отчёт!")
        return

    with open("ym_march_services_raw.xlsx", "wb") as f:
        f.write(content)
    print("  Сырой отчёт сохранён: ym_march_services_raw.xlsx")

    rev_df, costs_pivot, svc_totals = parse_report(content)

    if rev_df.empty:
        print("Нет данных о выручке!")
        return

    total_rev = rev_df["revenue"].sum()
    total_qty = rev_df["qty"].sum()

    print(f"\n=== ВЫРУЧКА (цена карточки × шт) ===")
    print(f"SKU:                       {len(rev_df)}")
    print(f"Штук:                      {total_qty:,.0f}")
    print(f"Выручка:                   {total_rev:,.0f} ₽")

    # Затраты
    SVC_ORDER = ["Размещение", "Буст", "Доставка", "Хранение", "Перевод",
                 "Приём", "Транзит", "Обработка", "Вывоз", "Утилизация"]
    grand_total = 0
    print(f"\n=== ЗАТРАТЫ (гросс) ===")
    for label in SVC_ORDER:
        v = svc_totals.get(label, 0)
        grand_total += v
        if v > 0:
            print(f"{label:20s} {v:>12,.0f} ₽")
    print(f"{'─' * 35}")
    print(f"{'ИТОГО':20s} {grand_total:>12,.0f} ₽")

    # Себестоимость
    print(f"\n=== СЕБЕСТОИМОСТЬ ===")
    price = load_price(PRICE_FILE)
    cost_map = build_cost_map(price)
    rev_df["sebes_unit"] = rev_df["sku"].map(cost_map).fillna(0)
    rev_df["sebes_total"] = rev_df["sebes_unit"] * rev_df["qty"]
    total_sebes = rev_df["sebes_total"].sum()
    matched = (rev_df["sebes_unit"] > 0).sum()
    print(f"Себестоимость:             {total_sebes:,.0f} ₽")
    print(f"Совпало артикулов:         {matched}/{len(rev_df)}")

    # P&L
    profit = total_rev - grand_total - total_sebes
    margin = (profit / total_rev * 100) if total_rev else 0

    print(f"\n=== P&L ===")
    print(f"Выручка (карточка):        {total_rev:>12,.0f} ₽")
    print(f"Затраты ЯМ (гросс):       {grand_total:>12,.0f} ₽")
    print(f"Себестоимость:             {total_sebes:>12,.0f} ₽")
    print(f"{'─' * 45}")
    print(f"Прибыль:                   {profit:>12,.0f} ₽")
    print(f"Маржа:                     {margin:>11.1f}%")

    # Сверка
    print(f"\n=== СВЕРКА С ЛК ===")
    expected = {
        "Штук": (total_qty, 2656),
        "Выручка": (total_rev, 4354585),
        "Затраты гросс": (grand_total, 2674236),
        "Себестоимость": (total_sebes, 1595877),
        "Прибыль": (profit, 84472),
        "Размещение": (svc_totals.get("Размещение", 0), 1243381),
        "Буст": (svc_totals.get("Буст", 0), 489936),
        "Доставка": (svc_totals.get("Доставка", 0), 524979),
        "Хранение": (svc_totals.get("Хранение", 0), 324782),
        "Перевод": (svc_totals.get("Перевод", 0), 69219),
    }
    for name, (actual, exp) in expected.items():
        diff = actual - exp
        pct = (diff / exp * 100) if exp else 0
        mark = "✓" if abs(pct) < 3 else "△" if abs(pct) < 10 else "✗"
        print(f"  {mark} {name:25s}  факт: {actual:>12,.0f}  ожид: {exp:>12,.0f}  "
              f"Δ: {diff:>+10,.0f} ({pct:>+.1f}%)")

    # Excel
    print(f"\n=== EXCEL ===")
    merged = rev_df.copy()
    if not costs_pivot.empty:
        merged = merged.merge(costs_pivot, on="sku", how="left").fillna(0)

    # Прочее = сумма мелких услуг
    main_svcs = ["Размещение", "Буст", "Доставка", "Хранение", "Перевод"]
    other_svcs = [s for s in SVC_ORDER if s not in main_svcs and s in merged.columns]
    if other_svcs:
        merged["Прочее"] = merged[other_svcs].sum(axis=1)
    else:
        merged["Прочее"] = 0

    cost_cols = [s for s in main_svcs if s in merged.columns] + ["Прочее"]
    merged["Затраты"] = merged[cost_cols].sum(axis=1)
    merged["Прибыль"] = merged["revenue"] - merged["Затраты"] - merged["sebes_total"]
    merged["Маржа %"] = (merged["Прибыль"] / merged["revenue"].replace(0, 0.001) * 100).round(1)

    out_cols = ["sku", "name", "qty", "revenue"] + cost_cols + [
        "Затраты", "sebes_unit", "sebes_total", "Прибыль", "Маржа %"]
    out = merged[[c for c in out_cols if c in merged.columns]].copy()
    out = out.sort_values("revenue", ascending=False).reset_index(drop=True)

    rename = {
        "sku": "Артикул", "name": "Название", "qty": "Шт",
        "revenue": "Выручка",
        "Затраты": "Затраты итого",
        "sebes_unit": "Себест/шт", "sebes_total": "Себестоимость",
    }
    out = out.rename(columns=rename)

    filename = "ym_march_2026_check.xlsx"
    out.to_excel(filename, index=False, sheet_name="Март 2026")
    print(f"Сохранено: {filename} ({len(out)} строк)")
    print("\nГотово.")


if __name__ == "__main__":
    main()
