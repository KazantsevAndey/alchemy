"""Месячное закрытие Яндекс Маркета: маржа по закрывающим документам + сверка.

Считает маржу за календарный месяц по методике «нетто» (см. tools/README_ЯМ_маржа.md)
и сверяет её с тремя независимыми источниками Маркета, чтобы цифра сходилась до копейки:

  1. Отчёт о стоимости услуг ПО ДАТЕ АКТА (united-marketplace-services, yearFrom/monthFrom)
     — это детализация акта об оказанных услугах. Даёт «Ваша цена × шт», совместные акции
     и все услуги.                                                  → ОСНОВА РАСЧЁТА
  2. Отчёт по реализации (goods-realization, по кампаниям FBY/FBS) — закрывающий документ.
     «Доставленные товары, без учёта скидок» должен равняться «Ваша цена × шт» из п.1.
  3. Отчёт о платежах (united-netting) — деньги. По каждой строке заказ+SKU:
     платёж покупателя + баллы = «Ваша цена × шт»; списания «Скидка за участие в совместных
     акциях» = совместные акции из п.1; строка «Оплата услуг Маркета» с датой акта
     = сумма услуг из п.1.
  4. Отчёт по баллам (united-netting, monthOfYear) — справочно: баллы нетто = совместные акции.

Запуск на проде (от пользователя alchemy, из корня проекта):
    venv/bin/python tools/ym_month_close.py --year 2026 --month 8 --user 1
    venv/bin/python tools/ym_month_close.py --year 2026 --month 8 --user 1 --no-fetch   # уже скачано

Файлы отчётов кладутся в data/ym_close/YYYY-MM/, туда же — ym_close_YYYY-MM.xlsx с юниткой по SKU.
Лимит Маркета: 1 отчёт в 2 минуты на каждый тип отчёта, поэтому скачивание ~10 минут.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import os
import sys
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ym_margin import (  # noqa: E402
    YM_BASE, _find_header_row, _generate_report, _headers, _ym_headers,
    norm_sku, parse_services_report, SVC_ORDER,
)

WAIT = 135  # секунд между запросами одного типа отчёта (лимит 1 / 2 мин)
PROMO_SRC = "совместных акциях"


# ── Скачивание ───────────────────────────────────────────────────────

def fetch_all(creds: dict, year: int, month: int, out_dir: str) -> dict:
    """Скачать все отчёты месяца в out_dir. Возвращает {ключ: путь}."""
    os.makedirs(out_dir, exist_ok=True)
    bid = creds["YM_BUSINESS_ID"]
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    # Платежи: платёж покупателя датируется оформлением заказа (за 1–6 недель до доставки),
    # возвраты — после. Берём широкое окно.
    net_from = (first - dt.timedelta(days=60)).isoformat()
    net_to = min(last + dt.timedelta(days=14), dt.date.today()).isoformat()

    campaigns = requests.get(f"{YM_BASE}/campaigns", headers=_ym_headers(creds), timeout=30).json()
    camp_ids = [c["id"] for c in campaigns.get("campaigns", [])]

    jobs = [("svc_act", "united-marketplace-services",
             {"businessId": bid, "yearFrom": year, "monthFrom": month, "yearTo": year, "monthTo": month}),
            ("netting", "united-netting", {"businessId": bid, "dateFrom": net_from, "dateTo": net_to}),
            ("points", "united-netting", {"businessId": bid, "monthOfYear": {"year": year, "month": month}})]
    for cid in camp_ids:
        jobs.append((f"real_{cid}", "goods-realization", {"campaignId": cid, "year": year, "month": month}))

    files, last_call = {}, {}
    for key, ep, payload in jobs:
        fn = os.path.join(out_dir, f"{key}.xlsx")
        if os.path.exists(fn) and os.path.getsize(fn) > 1000:
            files[key] = fn
            continue
        if ep in last_call:
            wait = WAIT - (time.time() - last_call[ep])
            if wait > 0:
                print(f"  жду {wait:.0f} с (лимит {ep})", flush=True)
                time.sleep(wait)
        content = None
        # Реализация по чужой кампании (другой ИНН, напр. Tookytoy) никогда не отдаёт файл —
        # одна попытка и идём дальше; остальные отчёты пробуем трижды.
        for _ in range(1 if ep == "goods-realization" else 3):
            last_call[ep] = time.time()
            content = _generate_report(ep, payload, creds)
            if content:
                break
            time.sleep(WAIT)
        if not content:
            print(f"  !! не удалось скачать {key}" + (" (чужая кампания? пропускаю)" if ep == "goods-realization" else ""), flush=True)
            continue
        with open(fn, "wb") as f:
            f.write(content)
        files[key] = fn
        print(f"  скачан {key} ({len(content) // 1024} КБ)", flush=True)
    return files


# ── Разбор ───────────────────────────────────────────────────────────

def _sheet(xlsx: pd.ExcelFile, name: str) -> pd.DataFrame:
    d = xlsx.parse(name, header=None)
    row, _ = _find_header_row(d)
    hdr = _headers(d, row)
    df = d.iloc[row + 1:].copy()
    df.columns = hdr
    df = df[df["номер заказа или отгрузки"].astype(str).str.match(r"^\d{6,}")]
    df["order"] = df["номер заказа или отгрузки"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    df["sku"] = df["ваш sku"].map(norm_sku)
    return df


def _col(df: pd.DataFrame, *keys: str):
    for c in df.columns:
        if all(k in str(c) for k in keys):
            return c
    return None


def services_by_line(fn: str) -> pd.DataFrame:
    """Строки заказ+SKU из отчёта об услугах: qty, rev (Ваша цена×шт), promo (3 листа)."""
    x = pd.ExcelFile(fn)
    P = _sheet(x, "Размещение товаров и услуг")
    P["qty"] = pd.to_numeric(P[_col(P, "количество")], errors="coerce")
    P["price"] = pd.to_numeric(P[_col(P, "ваша цена")], errors="coerce")
    P["rev"] = P.qty * P.price
    P["promo"] = pd.to_numeric(P[_col(P, "скидка за участие")], errors="coerce").fillna(0)
    out = P.groupby(["order", "sku"]).agg(qty=("qty", "sum"), rev=("rev", "sum"), promo=("promo", "sum")).reset_index()
    for sn in x.sheet_names:
        if sn.startswith("Буст") or sn.startswith("Доставка (средняя"):
            B = _sheet(x, sn)
            pc = _col(B, "скидка за участие")
            if pc is None:
                continue
            B["p"] = pd.to_numeric(B[pc], errors="coerce").fillna(0)
            extra = B.groupby(["order", "sku"])["p"].sum().reset_index()
            out = out.merge(extra, how="outer", on=["order", "sku"])
            out["promo"] = out["promo"].fillna(0) + out["p"].fillna(0)
            out = out.drop(columns="p")
    return out.fillna({"qty": 0, "rev": 0, "promo": 0})


def netting_lines(fn: str) -> pd.DataFrame:
    d = pd.read_excel(fn, sheet_name="Отчёт о платежах", header=None)
    hdr = [str(v).strip() for v in d.iloc[1].tolist()]
    N = d.iloc[2:].copy()
    N.columns = hdr
    N = N[N["Дата транзакции"].notna()]
    N["order"] = N["Номер заказа или отгрузки"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    N["sku"] = N["Ваш SKU"].map(lambda v: norm_sku(v) if pd.notna(v) else "")
    N["amt"] = pd.to_numeric(N["Сумма транзакции, ₽"], errors="coerce").fillna(0)
    N["tdate"] = pd.to_datetime(N["Дата транзакции"], format="%d.%m.%Y %H:%M", errors="coerce")
    N["adate"] = pd.to_datetime(N["Дата акта об оказанных услугах"], format="%d.%m.%Y", errors="coerce")
    N["src"] = N["Источник транзакции"].fillna("")
    return N


def realization_totals(fn: str) -> dict:
    """Сводный отчёт по реализации: доставлено шт и сумма без скидок (физлица + бизнес)."""
    s = pd.read_excel(fn, sheet_name="Сводный отчёт", header=None)
    for i in range(len(s)):
        if str(s.iloc[i, 1]).startswith("Всего доставлено"):
            r = s.iloc[i].tolist()
            f = lambda v: float(v) if str(v) != "nan" else 0.0  # noqa: E731
            return {"qty": f(r[2]), "gross": f(r[3]) + f(r[5]), "paid": f(r[4]) + f(r[6])}
    return {"qty": 0.0, "gross": 0.0, "paid": 0.0}


# ── Расчёт и сверка ──────────────────────────────────────────────────

def close_month(files: dict, year: int, month: int, cost_map: dict, out_xlsx: str | None = None) -> dict:
    first = pd.Timestamp(year, month, 1)
    nxt = first + pd.offsets.MonthBegin(1)
    ok = lambda d: "✓" if abs(d) < 0.005 else "✗"  # noqa: E731
    print("=" * 66)
    print(f"ЯНДЕКС МАРКЕТ — ЗАКРЫТИЕ {month:02d}.{year}")
    print("=" * 66)

    # 1. Основа: отчёт об услугах по акту
    content = open(files["svc_act"], "rb").read()
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(buf):
        rev, costs, totals, info = parse_services_report(content)
    gross = float(rev["revenue"].sum())
    promo = float(totals.get("Совместные акции", 0.0))
    services = float(sum(v for k, v in totals.items() if k != "Совместные акции"))
    net = gross - promo - services
    print(f"1. Отчёт об услугах по акту (детализация акта):")
    print(f"   Ваша цена × шт (гросс)      {gross:>14,.2f}   ({int(rev['qty'].sum())} шт, {len(rev)} SKU)")
    print(f"   Совместные акции (списание) {promo:>14,.2f}   {promo / gross * 100:5.1f}%")
    for k in SVC_ORDER:
        if totals.get(k):
            print(f"     {k:26s} {totals[k]:>14,.2f}   {totals[k] / gross * 100:5.1f}%")
    print(f"   Услуги по акту              {services:>14,.2f}   {services / gross * 100:5.1f}%")
    print(f"   НЕТТО                       {net:>14,.2f}   {net / gross * 100:5.1f}% от гросса")

    # 2. Реализация
    real = {"qty": 0.0, "gross": 0.0, "paid": 0.0}
    for k, fn in files.items():
        if k.startswith("real_"):
            t = realization_totals(fn)
            for kk in real:
                real[kk] += t[kk]
    d_real = real["gross"] - gross
    print(f"\n2. Отчёт по реализации (закрывающий): доставлено {real['qty']:.0f} шт, "
          f"без скидок {real['gross']:,.2f}, покупатели заплатили {real['paid']:,.2f}")
    print(f"   {ok(d_real)} без скидок − гросс из п.1 = {d_real:,.2f}")

    # 3. Платежи
    lines = services_by_line(files["svc_act"])
    N = netting_lines(files["netting"])
    accr = N[N.src.str.contains("Платёж покупателя|Баллы|Компенсац", regex=True)]
    accr = accr.groupby(["order", "sku"])["amt"].sum().rename("accr")
    pr = N[N.src.str.contains(PROMO_SRC)].groupby(["order", "sku"])["amt"].sum().rename("promo_net")
    J = lines.merge(accr.reset_index(), how="left", on=["order", "sku"]).merge(pr.reset_index(), how="left", on=["order", "sku"])
    J["promo_net"] = -J["promo_net"].fillna(0)
    sold = J[J.qty > 0]
    d_rev = sold.rev - sold.accr.fillna(0)
    d_pr = sold.promo - sold.promo_net
    n_bad_rev, n_bad_pr = int((d_rev.abs() > 0.5).sum()), int((d_pr.abs() > 0.5).sum())
    month_tx = N[(N.tdate >= first) & (N.tdate < nxt)]
    promo_month = -float(month_tx[month_tx.src.str.contains(PROMO_SRC)].amt.sum())
    act_rows = N[(N.src == "Оплата услуг Маркета") & (N.adate >= first) & (N.adate < nxt)]
    act_sum = -float(act_rows.amt.sum())
    print(f"\n3. Отчёт о платежах:")
    print(f"   по строкам заказ+SKU ({len(sold)}): платёж+баллы = Ваша цена×шт — "
          f"{ok(d_rev.abs().sum() if n_bad_rev == 0 else 1)} расхождений {n_bad_rev}, "
          f"сумма {sold.accr.fillna(0).sum():,.2f} против {sold.rev.sum():,.2f}")
    print(f"   по строкам: списания «совместные акции» = п.1 — {ok(1 if n_bad_pr else 0)} расхождений {n_bad_pr}")
    print(f"   {ok(promo_month - promo)} списания «совместные акции» с датой в месяце {promo_month:,.2f} "
          f"− п.1 {promo:,.2f} = {promo_month - promo:,.2f}")
    if len(act_rows):
        print(f"   {ok(act_sum - services)} «Оплата услуг Маркета» по акту {act_rows['Номер акта об оказанных услугах'].iloc[0]} "
              f"{act_sum:,.2f} − услуги п.1 {services:,.2f} = {act_sum - services:,.2f}")
    else:
        print("   акт «Оплата услуг Маркета» за месяц в платежах ещё не появился (приходит в первые дни следующего месяца)")
    if n_bad_rev:
        bad = sold[d_rev.abs() > 0.5]
        print("   строки с расхождением по выручке (обычно платёж покупателя старше окна отчёта):")
        print(bad[["order", "sku", "qty", "rev", "accr"]].head(10).to_string(index=False))

    # 4. Баллы
    if "points" in files:
        P = pd.read_excel(files["points"], sheet_name="Отчёт по баллам", header=None)
        ph = [str(v).strip() for v in P.iloc[1].tolist()]
        P = P.iloc[2:].copy()
        P.columns = ph
        P["amt"] = pd.to_numeric(P["Сумма транзакции, ₽"], errors="coerce").fillna(0)
        # Баллы датируются оформлением заказа, поэтому режем по дате ДОСТАВКИ — как отчёт об услугах
        P["ddate"] = pd.to_datetime(P["Дата доставки заказа"], format="%d.%m.%Y", errors="coerce")
        Pm = P[(P.ddate >= first) & (P.ddate < nxt)]
        pts = float(Pm[Pm["Источник транзакции"].fillna("").str.startswith("Баллы")].amt.sum()
                    + Pm[Pm["Источник транзакции"].fillna("").str.startswith("Возврат баллов")].amt.sum())
        prem = float(Pm[Pm["Источник транзакции"].fillna("").str.contains("Премия")].amt.sum())
        print(f"\n4. Баллы по доставкам месяца (нетто) {pts:,.2f} (≈ совместным акциям по строкам); премия {prem:,.2f}"
              + ("  ← премия есть, добавить к нетто" if prem else ""))

    # 5. Маржа
    R = rev.merge(costs, on="sku", how="left").fillna(0)
    R["sebes_unit"] = R["sku"].map(lambda s: cost_map.get(s, cost_map.get(s + ".0")))
    R["sebes_unit"] = pd.to_numeric(R["sebes_unit"], errors="coerce")
    R["sebes_total"] = R["sebes_unit"] * R["qty"]
    svc_cols = [c for c in SVC_ORDER if c in R.columns and c != "Совместные акции"]
    R["Услуги"] = R[svc_cols].sum(axis=1)
    R["Нетто"] = R["revenue"] - R["Совместные акции"] - R["Услуги"]
    R["Прибыль"] = R["Нетто"] - R["sebes_total"]
    R["Маржа_pct"] = (R["Прибыль"] / R["Нетто"] * 100).where(R["Нетто"] > 0).round(1)
    R = R.sort_values("revenue", ascending=False)
    sebes = float(R["sebes_total"].fillna(0).sum())
    no_cost = R[R.sebes_unit.isna() & (R.qty > 0)]
    profit = net - sebes
    margin = profit / net * 100 if net else 0
    kpi = (net - sebes * 1.04) / net * 100 if net else 0
    print(f"\n5. МАРЖА: нетто {net:,.2f} − себестоимость {sebes:,.2f} = прибыль {profit:,.2f}")
    print(f"   маржа от нетто {margin:.1f}%   KPI (себес ×1,04) {kpi:.1f}%   маржа от гросса {profit / gross * 100:.1f}%")
    if len(no_cost):
        print(f"   !! нет себестоимости у {len(no_cost)} SKU: {', '.join(no_cost.sku)}")
    if out_xlsx:
        cols = ["sku", "name", "qty", "revenue", "Совместные акции", "Услуги", "Нетто", "sebes_unit", "sebes_total", "Прибыль", "Маржа_pct"]
        R[cols].rename(columns={"revenue": "Ваша цена×шт", "sebes_unit": "Себес/шт", "sebes_total": "Себестоимость"}).to_excel(out_xlsx, index=False)
        print(f"   юнитка по SKU: {out_xlsx}")
    return {"gross": gross, "promo": promo, "services": services, "net": net, "sebes": sebes,
            "profit": profit, "margin": margin, "kpi": kpi, "real_gross": real["gross"],
            "promo_month_netting": promo_month, "act_netting": act_sum}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--user", type=int, default=1, help="user_id для себестоимости и ключей")
    ap.add_argument("--dir", default=None, help="папка с отчётами (по умолчанию data/ym_close/YYYY-MM)")
    ap.add_argument("--no-fetch", action="store_true", help="не скачивать, взять файлы из папки")
    a = ap.parse_args()
    out_dir = a.dir or os.path.join(ROOT, "data", "ym_close", f"{a.year}-{a.month:02d}")
    from user_context import get_user_credentials
    from db import get_cost_map
    creds = get_user_credentials(a.user)
    if a.no_fetch:
        files = {os.path.splitext(f)[0]: os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".xlsx") and not f.startswith("ym_close")}
    else:
        files = fetch_all(creds, a.year, a.month, out_dir)
    if "svc_act" not in files or "netting" not in files:
        print("нет обязательных отчётов svc_act / netting")
        sys.exit(1)
    close_month(files, a.year, a.month, get_cost_map(a.user),
                out_xlsx=os.path.join(out_dir, f"ym_close_{a.year}-{a.month:02d}.xlsx"))


if __name__ == "__main__":
    main()
