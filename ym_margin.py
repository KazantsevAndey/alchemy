"""
Маржинальность Яндекс Маркет.

Методика (та же, что для Ozon/WB — маржа от выручки НЕТТО):
  - Выручка гросс = «Ваша цена за товар» × «Количество» из листа
    «Размещение товаров и услуг» отчёта united-marketplace-services.
    Это цена продавца; скидки/баллы Маркета («Разница между вашей ценой
    и ценой продажи») продавца не касаются и в расчёт не входят.
  - Удержания = «Стоимость услуги» по всем листам отчёта
    ПЛЮС колонка «Скидка за участие в совместных акциях».
    Важно: в отчёте по услугам эта «скидка» уменьшает стоимость услуги
    (размещение 36% превращается в ~3%), но Маркет списывает её обратно
    через баланс продавца строкой «Скидка за участие в совместных акциях»
    (видно в отчёте о платежах / взаиморасчётах). Проверено по 304
    оплаченным заказам за 28.07–26.08.2026: выплата сошлась с точностью
    до рублей. Поэтому в расчёте она идёт отдельной строкой удержаний
    «Совместные акции» — это аналог соинвеста Ozon.
  - Нетто = гросс − удержания.
  - Прибыль = нетто − себестоимость;  маржа = прибыль / нетто.

Хранение и услуги без SKU (транзит, обработка невыкупов) учитываются
в ИТОГЕ по кабинету, но НЕ в юнит-экономике по SKU: хранение начисляется
за товар на складе, а не за проданную штуку — иначе медленный товар
показывает фиктивный убыток.

Вход:
  - Яндекс Маркет Partner API (отчёт united-marketplace-services)
  - Прайс (себестоимость) — cost_map {артикул: себестоимость}

Выход:
  - DataFrame с юнит-экономикой по SKU
  - dict totals {услуга: сумма} по кабинету
  - dict info  (размещение по тарифу, кол-во SKU без себестоимости и т.п.)
"""

import io
import re
import time

import pandas as pd
import requests

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


# Порядок услуг для отображения
SVC_ORDER = [
    "Размещение", "Совместные акции", "Буст", "Доставка", "Средняя миля",
    "Перевод", "Хранение", "Приём", "Транзит", "Обработка", "Вывоз", "Утилизация",
]

# Услуги, которые показываем отдельными колонками в таблице по SKU.
# Остальные SKU-услуги → «Прочее».
SVC_MAIN = ["Размещение", "Совместные акции", "Буст", "Доставка", "Средняя миля", "Перевод"]

# Услуги, которые НЕ разносятся на проданную штуку (только в итог кабинета)
SVC_TOTAL_ONLY = ["Хранение", "Транзит", "Обработка", "Вывоз", "Утилизация"]

# Лист отчёта → (метка услуги, ключевые слова колонки стоимости).
# Сопоставление по началу названия листа — Маркет периодически переименовывает.
SHEET_MAP = [
    ("Размещение",                   "Размещение",   ["стоимость услуги ("]),
    ("Буст продаж",                  "Буст",         ["стоимость услуги"]),
    ("Доставка (средняя миля)",      "Средняя миля", ["стоимость услуги"]),
    ("Доставка покупателю",          "Доставка",     ["стоимость услуги"]),
    ("Перевод платежа",              "Перевод",      ["стоимость услуги"]),
    ("Приём платежа",                "Приём",        ["стоимость услуги"]),
    ("Платное хранение",             "Хранение",     ["стоимость платного хранения", "стоимость услуги"]),
    ("Поставка через транзитный",    "Транзит",      ["стоимость услуги"]),
    ("Обработка заказов",            "Обработка",    ["стоимость услуги"]),
    ("Вывоз",                        "Вывоз",        ["стоимость услуги"]),
    ("Организация утилизации",       "Утилизация",   ["стоимость услуги"]),
]


# ── Генерация отчётов ────────────────────────────────────────────────

def _generate_report(endpoint: str, payload: dict, creds=None) -> bytes | None:
    url = f"{YM_BASE}/reports/{endpoint}/generate"
    headers = _ym_headers(creds)
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"  Ошибка генерации {endpoint}: {resp.status_code} — {resp.text[:200]}")
        return None

    report_id = resp.json()["result"]["reportId"]
    done_no_url_streak = 0  # счётчик подряд DONE без URL — YM иногда так залипает
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
            file_url = result.get("file") or result.get("downloadUrl") or result.get("url")
            if not file_url:
                done_no_url_streak += 1
                if done_no_url_streak >= 10:
                    print(f"  Отчёт {endpoint}: 10 раз подряд DONE без URL — сдаёмся")
                    return None
                print(f"  DONE без file URL, жду 5 сек… (попытка {done_no_url_streak}/10)")
                time.sleep(5)
                continue
            return requests.get(file_url, timeout=60).content
        if st == "FAILED":
            print(f"  Отчёт FAILED: {r.json()}")
            return None
        done_no_url_streak = 0  # сбросить счётчик если статус не DONE

    print("  Таймаут генерации отчёта")
    return None


# ── Утилиты парсинга ─────────────────────────────────────────────────

def norm_sku(val) -> str:
    """Артикул к строке: '8000604002754.0' → '8000604002754'."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() == "nan":
        return ""
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    return s


def _find_col(headers, keywords, last=True):
    """Индекс колонки, в названии которой есть одно из ключевых слов.

    Ключевые слова проверяются по очереди; среди совпадений берётся
    последнее (last=True) — в отчёте ЯМ итоговая «Стоимость услуги»
    всегда правее промежуточных «без скидок», «до мин. тарифа» и т.п.
    """
    for kw in keywords:
        kw_l = kw.lower()
        hits = [i for i, h in enumerate(headers) if kw_l in h]
        if hits:
            return hits[-1] if last else hits[0]
    return None


def _find_header_row(df_raw, max_rows=12):
    """(строка заголовка, колонка SKU или None).

    Сначала ищем строку с «Ваш SKU». Если листа по SKU нет (транзит,
    обработка) — берём строку, где есть «Стоимость».
    """
    for i in range(min(max_rows, len(df_raw))):
        row = [str(v).lower().strip() for v in df_raw.iloc[i].tolist()]
        for j, cell in enumerate(row):
            if cell in ("ваш sku", "артикул", "sku"):
                return i, j
    for i in range(min(max_rows, len(df_raw))):
        row = [str(v).lower().strip() for v in df_raw.iloc[i].tolist()]
        if any("стоимость" in c for c in row):
            return i, None
    return None, None


def _headers(df_raw, row):
    """Заголовки листа. У «Размещения» заголовок двухэтажный: часть названий
    («Скидка за участие в совместных акциях», «Индивидуальная скидка…»)
    лежит строкой ниже под пустой ячейкой — подмешиваем их."""
    top = [str(v).lower().strip() for v in df_raw.iloc[row].tolist()]
    if row + 1 < len(df_raw):
        sub = [str(v).lower().strip() for v in df_raw.iloc[row + 1].tolist()]
        is_subheader = any(("скидка" in c or "тариф" in c) for c in sub) and \
            not any(c.replace(".", "").isdigit() and len(c) > 6 for c in sub)
        if is_subheader:
            top = [t if t != "nan" else s for t, s in zip(top, sub)]
    return top


def _num(v) -> float:
    x = pd.to_numeric(v, errors="coerce")
    return 0.0 if pd.isna(x) else float(x)


def _sheet_label(sheet_name):
    for prefix, label, kws in SHEET_MAP:
        if sheet_name.startswith(prefix):
            return label, kws
    return None, None


# ── Парсинг отчёта по услугам ────────────────────────────────────────

def parse_services_report(content: bytes):
    """Разобрать xlsx отчёта united-marketplace-services.

    Returns: (rev_df, costs_pivot, totals, info)
      rev_df:      DataFrame [sku, name, qty, revenue, Скидка ЯМ]
      costs_pivot: DataFrame [sku, Размещение, Буст, ..., Размещение полное]
      totals:      dict {услуга: фактически списано}
      info:        dict {placement_full, placement_fact, unknown_sheets, ...}
    """
    xlsx = pd.ExcelFile(io.BytesIO(content))

    rev_rows = []
    cost_rows = []
    totals = {}
    info = {"placement_full": 0.0, "placement_fact": 0.0, "promo_by_sheet": {},
            "unknown_sheets": [], "sheets": list(xlsx.sheet_names)}
    PROMO_KW = ["скидка за участие в совместных акциях"]

    for sn in xlsx.sheet_names:
        if sn.strip().lower() == "сводка":
            continue
        label, kws = _sheet_label(sn)
        if label is None:
            info["unknown_sheets"].append(sn)
            # Не теряем деньги молча: пробуем снять итог по колонке «Стоимость»
            kws = ["стоимость услуги", "стоимость"]
            label = f"Прочее: {sn[:30]}"

        df_raw = pd.read_excel(xlsx, sheet_name=sn, header=None)
        hdr_row, sku_col = _find_header_row(df_raw)
        if hdr_row is None:
            continue
        hdrs = _headers(df_raw, hdr_row)
        cost_col = _find_col(hdrs, kws)
        if cost_col is None:
            cost_col = _find_col(hdrs, ["стоимость"])
        if cost_col is None:
            continue

        body = df_raw.iloc[hdr_row + 1:]
        promo_col = _find_col(hdrs, PROMO_KW)

        def _promo(row):
            return _num(row.iloc[promo_col]) if promo_col is not None else 0.0

        # ── Размещение: выручка + факт + полный тариф ──
        if label == "Размещение":
            price_col = _find_col(hdrs, ["ваша цена за товар", "ваша цена за шт", "ваша цена"], last=False)
            qty_col = _find_col(hdrs, ["количество, шт", "количество"], last=False)
            name_col = _find_col(hdrs, ["название товара"], last=False)
            disc_col = _find_col(hdrs, ["разница между вашей ценой"], last=False)
            full_col = _find_col(hdrs, ["стоимость услуги без скидок"], last=False)
            if sku_col is None or price_col is None or qty_col is None:
                print(f"  Лист «{sn}»: не нашёл колонки цены/количества — выручка не посчитана")
                continue
            for _, r in body.iterrows():
                sku = norm_sku(r.iloc[sku_col])
                if not sku:
                    continue
                p = _num(r.iloc[price_col])
                q = _num(r.iloc[qty_col])
                fee = _num(r.iloc[cost_col])
                full = _num(r.iloc[full_col]) if full_col is not None else fee
                nm = str(r.iloc[name_col]).strip() if name_col is not None and pd.notna(r.iloc[name_col]) else ""
                disc = _num(r.iloc[disc_col]) if disc_col is not None else 0.0
                rev_rows.append({"sku": sku, "name": nm, "qty": int(q),
                                 "revenue": p * q, "Скидка ЯМ": disc})
                promo = _promo(r)
                cost_rows.append({"sku": sku, "service": "Размещение", "cost": fee})
                cost_rows.append({"sku": sku, "service": "Совместные акции", "cost": promo})
                cost_rows.append({"sku": sku, "service": "Размещение полное", "cost": full})
                totals["Размещение"] = totals.get("Размещение", 0.0) + fee
                totals["Совместные акции"] = totals.get("Совместные акции", 0.0) + promo
                info["promo_by_sheet"][label] = info["promo_by_sheet"].get(label, 0.0) + promo
                info["placement_full"] += full
                info["placement_fact"] += fee
            continue

        # ── Остальные листы ──
        if sku_col is not None:
            for _, r in body.iterrows():
                sku = norm_sku(r.iloc[sku_col])
                if not sku:
                    continue
                v = _num(r.iloc[cost_col])
                cost_rows.append({"sku": sku, "service": label, "cost": v})
                totals[label] = totals.get(label, 0.0) + v
                promo = _promo(r)
                if promo:
                    cost_rows.append({"sku": sku, "service": "Совместные акции", "cost": promo})
                    totals["Совместные акции"] = totals.get("Совместные акции", 0.0) + promo
                    info["promo_by_sheet"][label] = info["promo_by_sheet"].get(label, 0.0) + promo
        else:
            total = pd.to_numeric(body.iloc[:, cost_col], errors="coerce").fillna(0).sum()
            if total:
                totals[label] = totals.get(label, 0.0) + float(total)

    if rev_rows:
        rev_df = (pd.DataFrame(rev_rows)
                  .groupby("sku")
                  .agg(name=("name", "first"), qty=("qty", "sum"),
                       revenue=("revenue", "sum"), **{"Скидка ЯМ": ("Скидка ЯМ", "sum")})
                  .reset_index())
        # Название: берём первое непустое
        names = (pd.DataFrame(rev_rows).query("name != ''")
                 .groupby("sku")["name"].first())
        rev_df["name"] = rev_df["sku"].map(names).fillna(rev_df["name"])
    else:
        rev_df = pd.DataFrame(columns=["sku", "name", "qty", "revenue", "Скидка ЯМ"])

    if cost_rows:
        costs_pivot = (pd.DataFrame(cost_rows)
                       .pivot_table(index="sku", columns="service", values="cost",
                                    aggfunc="sum", fill_value=0.0)
                       .reset_index())
        costs_pivot.columns.name = None
    else:
        costs_pivot = pd.DataFrame(columns=["sku"])

    # Пустые/отрицательные итоги (возвраты за доставку) оставляем как есть —
    # это реальные деньги; в круговой диаграмме app.py отсекает v <= 0.
    print(f"  Выручка: {len(rev_df)} SKU, {rev_df['qty'].sum():,.0f} шт, "
          f"{rev_df['revenue'].sum():,.0f} ₽")
    print(f"  Удержания: {sum(totals.values()):,.0f} ₽  "
          f"({', '.join(f'{k} {v:,.0f}' for k, v in totals.items())})")
    if info["unknown_sheets"]:
        print(f"  Незнакомые листы (учтены как «Прочее»): {info['unknown_sheets']}")

    return rev_df, costs_pivot, totals, info


def load_services_report(date_from: str, date_to: str, creds=None):
    """Заказать отчёт united-marketplace-services и разобрать его.

    Returns: (rev_df, costs_pivot, totals, info) — см. parse_services_report.
    """
    c = _get_creds(creds)
    content = _generate_report("united-marketplace-services", {
        "businessId": c["YM_BUSINESS_ID"],
        "dateTimeFrom": f"{date_from}T00:00:00+03:00",
        "dateTimeTo": f"{date_to}T23:59:59+03:00",
    }, creds)
    if not content:
        empty_rev = pd.DataFrame(columns=["sku", "name", "qty", "revenue", "Скидка ЯМ"])
        return empty_rev, pd.DataFrame(columns=["sku"]), {}, {"error": "report not generated"}
    return parse_services_report(content)


# ── Расчёт маржи ────────────────────────────────────────────────────

def _lookup_cost(cost_map: dict, sku: str):
    """Себестоимость по артикулу с учётом форматов '123', '123.0', ' 123 '."""
    if sku in cost_map:
        return cost_map[sku]
    for k in (sku + ".0", sku.strip()):
        if k in cost_map:
            return cost_map[k]
    return None


def summarize(R: pd.DataFrame, totals: dict) -> dict:
    """Итог по кабинету по методике «маржа от нетто».

    Нетто = гросс − ВСЕ удержания (включая хранение и услуги без SKU).
    Себестоимость — только по SKU, у которых она известна.
    """
    if R is None or R.empty:
        return {"gross": 0.0, "fees": 0.0, "rev": 0.0, "sebes": 0.0,
                "profit": 0.0, "margin": 0.0, "kpi": 0.0, "sku": 0, "sku_no_cost": 0}
    gross = float(R["revenue"].sum())
    fees = float(sum(totals.values()))
    net = gross - fees
    sebes = float(R["sebes_total"].fillna(0).sum())
    profit = net - sebes
    margin = profit / net * 100 if net else 0.0
    kpi = (net - sebes * 1.04) / net * 100 if net else 0.0
    sold = R[R["qty"] > 0]
    return {"gross": gross, "fees": fees, "rev": net, "sebes": sebes,
            "profit": profit, "margin": margin, "kpi": kpi,
            "sku": int(len(sold)),
            "sku_no_cost": int(sold["sebes_unit"].isna().sum())}


def calc_margin(rev_df, costs_pivot, totals, cost_map, period_name="", info=None):
    """Юнит-экономика по SKU.

    Колонки результата: sku, name, qty, revenue, Скидка ЯМ,
      Размещение, Буст, Доставка, Средняя миля, Перевод, Прочее,
      Удержания, Нетто, Хранение, Размещение полное,
      sebes_unit, sebes_total, Прибыль, Маржа_pct

    Маржа по SKU считается БЕЗ хранения (см. докстринг модуля);
    хранение показано отдельной колонкой и учтено в итоге (summarize).
    """
    if rev_df is None or rev_df.empty:
        return pd.DataFrame()

    R = rev_df.copy()
    if "Скидка ЯМ" not in R.columns:
        R["Скидка ЯМ"] = 0.0

    if costs_pivot is not None and not costs_pivot.empty and "sku" in costs_pivot.columns:
        R = R.merge(costs_pivot, on="sku", how="left")
        # SKU, по которым есть только затраты (например хранение без продаж)
        extra = set(costs_pivot["sku"]) - set(R["sku"])
        if extra:
            ex = costs_pivot[costs_pivot["sku"].isin(extra)].copy()
            ex["name"] = "(без продаж в периоде)"
            ex["qty"] = 0
            ex["revenue"] = 0.0
            ex["Скидка ЯМ"] = 0.0
            R = pd.concat([R, ex], ignore_index=True)

    for col in SVC_ORDER + ["Размещение полное"]:
        if col not in R.columns:
            R[col] = 0.0
    num_cols = [c for c in R.columns if c not in ("sku", "name")]
    R[num_cols] = R[num_cols].fillna(0.0)

    # Прочее = SKU-услуги вне SVC_MAIN и вне «только в итог»
    other_svcs = [s for s in R.columns
                  if s not in SVC_MAIN and s not in SVC_TOTAL_ONLY
                  and s not in ("sku", "name", "qty", "revenue", "Скидка ЯМ", "Размещение полное")
                  and (s in SVC_ORDER or s.startswith("Прочее:"))]
    R["Прочее"] = R[other_svcs].sum(axis=1) if other_svcs else 0.0

    R["Удержания"] = R[SVC_MAIN].sum(axis=1) + R["Прочее"]
    R["Нетто"] = R["revenue"] - R["Удержания"]

    # Себестоимость: NaN, если артикула нет в прайсе — не маскируем нулём
    R["sebes_unit"] = R["sku"].map(lambda s: _lookup_cost(cost_map, s))
    R["sebes_unit"] = pd.to_numeric(R["sebes_unit"], errors="coerce")
    R["sebes_total"] = R["sebes_unit"] * R["qty"]

    import numpy as np
    R["Прибыль"] = R["Нетто"] - R["sebes_total"]
    # Маржа от нетто имеет смысл только при положительном нетто: при нетто ≤ 0
    # деление переворачивает знак (убыток показывался как +595%).
    R["Маржа_pct"] = (R["Прибыль"] / R["Нетто"].where(R["Нетто"] > 0) * 100).round(1)
    R.loc[R["qty"] == 0, ["Прибыль", "Маржа_pct"]] = np.nan

    # Совместимость со старым кодом страницы
    R["Затраты"] = R["Удержания"]

    R = R.sort_values("revenue", ascending=False).reset_index(drop=True)

    s = summarize(R, totals)
    print(f"\n{'=' * 50}\n{period_name}\n{'=' * 50}")
    print(f"Выручка гросс:         {s['gross']:>12,.0f} ₽")
    for svc in SVC_ORDER:
        v = totals.get(svc, 0)
        if v:
            print(f"  {svc:20s}  {v:>12,.0f}   {v / s['gross'] * 100 if s['gross'] else 0:5.1f}%")
    for k, v in totals.items():
        if k not in SVC_ORDER and v:
            print(f"  {k:20s}  {v:>12,.0f}")
    print(f"  {'ИТОГО удержания':20s}  {s['fees']:>12,.0f}   {s['fees'] / s['gross'] * 100 if s['gross'] else 0:5.1f}%")
    print(f"Выручка нетто:         {s['rev']:>12,.0f} ₽")
    print(f"Себестоимость:         {s['sebes']:>12,.0f}   (нет у {s['sku_no_cost']} из {s['sku']} SKU)")
    print(f"Прибыль:               {s['profit']:>12,.0f}")
    print(f"Маржа от нетто:        {s['margin']:>11.1f}%   KPI (себес ×1,04): {s['kpi']:.1f}%")
    if info and info.get("promo_by_sheet"):
        print("Совместные акции по листам: " + ", ".join(f"{k} {v:,.0f}" for k, v in info["promo_by_sheet"].items()))

    return R


# ── Сохранение Excel ────────────────────────────────────────────────

EXPORT_COLS = (["sku", "name", "qty", "revenue"] + SVC_MAIN +
               ["Прочее", "Удержания", "Нетто", "sebes_unit", "sebes_total",
                "Прибыль", "Маржа_pct", "Хранение", "Размещение полное", "Скидка ЯМ"])

EXPORT_RENAME = {
    "sku": "Артикул", "name": "Название", "qty": "Шт",
    "revenue": "Выручка гросс", "Удержания": "Удержания ЯМ", "Нетто": "Выручка нетто",
    "sebes_unit": "Себест/шт", "sebes_total": "Себестоимость",
    "Маржа_pct": "Маржа %", "Хранение": "Хранение (в итоге)",
    "Размещение полное": "Размещение по тарифу", "Скидка ЯМ": "Скидка Маркета",
}


def export_frame(R: pd.DataFrame) -> pd.DataFrame:
    out = R[[c for c in EXPORT_COLS if c in R.columns]].copy()
    return out.rename(columns=EXPORT_RENAME)


def save_excel(R, totals, filename):
    if R is None or R.empty:
        print(f"Нет данных для {filename}")
        return
    export_frame(R).to_excel(filename, index=False, sheet_name="По SKU")
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

    print(f"\nЗагружаю данные за месяц ({dm} — {dy})...")
    rev_m, costs_m, totals_m, info_m = load_services_report(dm, dy)
    R_m = calc_margin(rev_m, costs_m, totals_m, cost_map, f"МЕСЯЦ ({dm} — {dy})", info_m)

    print("\nЖду 125 сек (лимит ЯМ: 1 отчёт в 2 минуты)...")
    time.sleep(125)

    print(f"\nЗагружаю данные за вчера ({dy})...")
    rev_y, costs_y, totals_y, info_y = load_services_report(dy, dy)
    R_y = calc_margin(rev_y, costs_y, totals_y, cost_map, f"ВЧЕРА ({dy})", info_y)

    save_excel(R_m, totals_m, "ym_margin_month.xlsx")
    save_excel(R_y, totals_y, "ym_margin_yesterday.xlsx")

    print("\nГотово.")


if __name__ == "__main__":
    main()
