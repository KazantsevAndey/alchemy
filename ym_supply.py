"""
Планирование поставок Яндекс Маркет (FBY/FBS).

Гибридный источник данных:
  1. Файл из ЛК ЯМ «Остатки по кластерам» (приоритет) — 100% точность
  2. Отчёт goods-turnover через API (фоллбэк) — ~97% точность

Кванты — quantum_stock.xlsx (матч по Артикул = offerId).
"""

import pandas as pd
import numpy as np
import requests
import time
import io
from datetime import datetime, timedelta

from utils.price_loader import load_price

# ── Настройки ────────────────────────────────────────────────────────────

DAYS_PLAN = 60
QUANTUM_FILE = "quantum_stock.xlsx"

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

# Маппинг кластеров из разных источников → единые имена Алхимии
_CLUSTER_MAP = {
    # goods-turnover report
    "Москва": "Москва МО",
    "Санкт-Петербург": "СПб СЗО",
    "Ростов": "Ростов",
    "Екатеринбург": "Екатеринбург",
    "Самара": "Самара",
    # ЛК-файл (может отличаться)
    "Москва МО": "Москва МО",
    "СПб СЗО": "СПб СЗО",
    "Москва, МО и Дальние регионы": "Москва МО",
    "Санкт-Петербург и СЗО": "СПб СЗО",
}


# ── Загрузка квантов ──────────────────────────────────────────────────────

def load_quants(path: str = QUANTUM_FILE) -> pd.DataFrame:
    df = pd.read_excel(path)
    df['Артикул'] = df['Артикул'].astype(str).str.strip()
    return df


# ── Парсинг файла из ЛК ─────────────────────────────────────────────────

def parse_lk_file(file_bytes: bytes) -> pd.DataFrame:
    """Парсит файл «Остатки по кластерам» из ЛК ЯМ.

    Ожидаемые колонки (ищет по ключевым словам):
      - SKU: SSKU, Ваш SKU, SKU, Артикул
      - Кластер
      - Остаток
      - Понедельные продажи (Нед1..Нед4) или Реал прод/день

    Возвращает: sku, cluster, name, daily, stock
    """
    df = pd.read_excel(io.BytesIO(file_bytes))
    cols = df.columns.tolist()
    cols_lower = {c: str(c).lower().strip() for c in cols}

    # Ищем колонки
    col_sku = _find_col(cols, cols_lower, ["ssku", "ваш sku", "sku", "артикул"])
    col_cluster = _find_col(cols, cols_lower, ["кластер"])
    col_name = _find_col(cols, cols_lower, ["название"])
    col_stock = _find_col(cols, cols_lower, ["остаток"])

    if not col_sku or not col_cluster:
        print(f"  ЛК-файл: не найдены колонки SKU/Кластер. Колонки: {cols}")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    # Daily rate: ищем прямую колонку или понедельные
    col_daily = _find_col(cols, cols_lower, ["реал прод/день", "прод/день", "среднесуточн"])
    week_cols = [c for c in cols if str(c).lower().strip().startswith("нед")]

    rows = []
    for _, r in df.iterrows():
        sku = str(r[col_sku]).strip()
        if not sku or sku == "nan":
            continue

        cluster_raw = str(r[col_cluster]).strip()
        if not cluster_raw or cluster_raw == "nan":
            continue

        cluster = _CLUSTER_MAP.get(cluster_raw, cluster_raw)

        name = str(r[col_name]).strip() if col_name and pd.notna(r.get(col_name)) else ""

        stock = 0
        if col_stock:
            try:
                stock = int(float(r[col_stock])) if pd.notna(r[col_stock]) else 0
            except (ValueError, TypeError):
                stock = 0

        # Daily rate
        daily = 0.0
        if col_daily:
            try:
                daily = float(r[col_daily]) if pd.notna(r[col_daily]) else 0.0
            except (ValueError, TypeError):
                daily = 0.0
        elif week_cols:
            # Сумма за 4 недели / 31 день (как в эталоне)
            total = 0
            for wc in week_cols:
                try:
                    val = float(r[wc]) if pd.notna(r[wc]) else 0
                except (ValueError, TypeError):
                    val = 0
                total += val
            n_weeks = len(week_cols)
            daily = round(total / (n_weeks * 7), 2) if n_weeks else 0.0

        rows.append({
            "sku": sku,
            "cluster": cluster,
            "name": name,
            "daily": round(daily, 2),
            "stock": stock,
        })

    if not rows:
        print("  ЛК-файл: нет данных после парсинга")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    result = pd.DataFrame(rows)
    # Агрегируем дубли
    result = result.groupby(["sku", "cluster"]).agg({
        "name": "first", "daily": "sum", "stock": "sum",
    }).reset_index()

    print(f"  ЛК-файл: {len(result)} строк, {result['sku'].nunique()} SKU, "
          f"{sorted(result['cluster'].unique().tolist())}")
    return result


def _find_col(cols, cols_lower, keywords):
    """Найти колонку по ключевым словам (первое совпадение)."""
    for kw in keywords:
        for c in cols:
            if kw in cols_lower[c]:
                return c
    return None


# ── Отчёт оборачиваемости (API фоллбэк) ────────────────────────────────

def load_turnover_report(creds=None) -> pd.DataFrame:
    """Скачать goods-turnover отчёт и вернуть DataFrame с daily/stock по SKU×кластер.

    Возвращает колонки: sku, cluster, name, daily, stock
    """
    c = _get_creds(creds)
    headers = _ym_headers(creds)
    print("  Генерация отчёта оборачиваемости ЯМ...")
    gen_resp = requests.post(
        f"{YM_BASE}/reports/goods-turnover/generate",
        headers=headers,
        json={"campaignId": c["YM_CAMPAIGN_ID"]},
        timeout=30,
    )

    if gen_resp.status_code not in (200, 201):
        print(f"  Ошибка генерации отчёта: {gen_resp.status_code} — {gen_resp.text[:300]}")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    report_id = gen_resp.json().get("result", {}).get("reportId", "")
    if not report_id:
        print(f"  Нет reportId: {gen_resp.text[:300]}")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    print(f"  Report ID: {report_id}, ждём готовности...")

    # Ждём готовности
    file_url = None
    for attempt in range(60):
        time.sleep(5)
        try:
            st_resp = requests.get(
                f"{YM_BASE}/reports/info/{report_id}",
                headers=headers, timeout=30,
            )
        except requests.exceptions.RequestException as e:
            print(f"  Ошибка проверки статуса: {e}")
            continue

        if st_resp.status_code != 200:
            continue

        info = st_resp.json().get("result", {})
        status = info.get("status", "")
        if status == "DONE":
            file_url = info.get("file", "")
            print(f"  Отчёт готов (попытка {attempt + 1})")
            break
        elif status == "FAILED":
            print(f"  Отчёт FAILED: {info}")
            return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    if not file_url:
        print("  Таймаут ожидания отчёта (5 мин)")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    # Скачиваем
    print("  Скачиваем отчёт...")
    try:
        dl_resp = requests.get(file_url, headers={"Api-Key": c["YM_API_KEY"]}, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"  Ошибка скачивания: {e}")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    if dl_resp.status_code != 200:
        print(f"  Ошибка скачивания: {dl_resp.status_code}")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    # Парсим все листы
    all_rows = []
    xls = pd.ExcelFile(io.BytesIO(dl_resp.content))

    for sheet_name in xls.sheet_names:
        if sheet_name == "Сводка":
            continue

        df_sheet = pd.read_excel(xls, sheet_name=sheet_name, header=8)
        if df_sheet.empty:
            continue

        cols = df_sheet.columns.tolist()
        col_cluster = cols[0]
        col_sku = cols[1]
        col_name = cols[3]

        # Ищем колонки по ключевым словам
        col_daily = None
        col_stock = None
        for c in cols:
            c_lower = str(c).lower()
            if "среднесуточное количество проданных" in c_lower:
                col_daily = c
            elif "остаток на последний день" in c_lower:
                col_stock = c

        if col_daily is None and len(cols) > 12:
            col_daily = cols[12]
        if col_stock is None and len(cols) > 14:
            col_stock = cols[14]

        if col_daily is None or col_stock is None:
            continue

        for _, row in df_sheet.iterrows():
            cluster_raw = str(row[col_cluster]).strip()
            sku = str(row[col_sku]).strip()

            if not sku or sku == "nan" or not cluster_raw or cluster_raw == "nan":
                continue

            name = str(row[col_name]) if pd.notna(row[col_name]) else ""

            try:
                daily = float(row[col_daily]) if pd.notna(row[col_daily]) else 0.0
            except (ValueError, TypeError):
                daily = 0.0

            try:
                stock = int(float(row[col_stock])) if pd.notna(row[col_stock]) else 0
            except (ValueError, TypeError):
                stock = 0

            cluster = _CLUSTER_MAP.get(cluster_raw, cluster_raw)

            all_rows.append({
                "sku": sku,
                "cluster": cluster,
                "name": name,
                "daily": round(daily, 2),
                "stock": stock,
            })

    if not all_rows:
        print("  Отчёт пуст")
        return pd.DataFrame(columns=["sku", "cluster", "name", "daily", "stock"])

    result = pd.DataFrame(all_rows)
    result = result.groupby(["sku", "cluster"]).agg({
        "name": "first", "daily": "sum", "stock": "sum",
    }).reset_index()

    print(f"  Отчёт оборачиваемости: {len(result)} строк, {result['sku'].nunique()} SKU, "
          f"{sorted(result['cluster'].unique().tolist())}")
    return result


# ── План поставок ─────────────────────────────────────────────────────────

def compute_ym_supply_data(days_plan: int = DAYS_PLAN,
                           quantum_file: str = QUANTUM_FILE,
                           lk_file_bytes: bytes = None):
    """Вычислить план поставок ЯМ.

    Если передан lk_file_bytes — парсит файл из ЛК (100% точность).
    Иначе — скачивает goods-turnover через API (~97% точность).

    Returns dict:
      cluster_priority: DataFrame
      plan: DataFrame
      source: str ("lk" или "api")
    """
    print("Загрузка квантов...")
    df_q = load_quants(quantum_file)
    quants = dict(zip(df_q['Артикул'], df_q['квант']))
    prices = dict(zip(df_q['Артикул'], df_q['Цена']))
    names_q = dict(zip(df_q['Артикул'], df_q['Название']))

    # Загружаем данные: ЛК-файл (приоритет) или API (фоллбэк)
    source = "api"
    if lk_file_bytes:
        print("Парсинг файла из ЛК ЯМ...")
        df_plan = parse_lk_file(lk_file_bytes)
        if not df_plan.empty:
            source = "lk"
        else:
            print("  ЛК-файл не удалось распарсить, переключаемся на API")

    if source == "api":
        print("Загрузка отчёта оборачиваемости ЯМ (API)...")
        df_plan = load_turnover_report()

    if df_plan.empty:
        print("  Нет данных")
        return None

    # stock_total = stock
    df_plan["stock_total"] = df_plan["stock"]
    df_plan["in_transit"] = 0
    df_plan["sold"] = (df_plan["daily"] * 30).round(0)

    # Названия: из квантов (приоритет) или из источника
    df_plan["name"] = df_plan.apply(
        lambda r: names_q.get(r["sku"], "") or r["name"] or "", axis=1
    )

    # Цены и кванты
    df_plan["price"] = df_plan["sku"].map(prices)
    df_plan["quant"] = df_plan["sku"].map(quants).fillna(1).astype(int)

    # Расчёт
    df_plan["days"] = (
        df_plan["stock_total"] / df_plan["daily"].replace(0, 0.0001)
    ).round(0).clip(upper=999)
    df_plan.loc[df_plan["daily"] == 0, "days"] = 999

    df_plan["need"] = (
        (df_plan["daily"] * days_plan) - df_plan["stock_total"]
    ).clip(lower=0).round(0)

    df_plan["boxes"] = np.ceil(df_plan["need"] / df_plan["quant"].replace(0, 1)).astype(int)
    df_plan.loc[(df_plan["need"] > 0) & (df_plan["boxes"] == 0), "boxes"] = 1
    df_plan.loc[
        (df_plan["stock_total"] == 0) & (df_plan["daily"] > 0) & (df_plan["boxes"] == 0),
        "boxes"
    ] = 1
    df_plan["order"] = df_plan["boxes"] * df_plan["quant"]

    # Приоритет кластеров
    cluster_sum = df_plan.groupby("cluster").agg({
        "stock_total": "sum", "sold": "sum", "daily": "sum", "order": "sum",
    }).reset_index()
    cluster_sum["days"] = (
        cluster_sum["stock_total"] / cluster_sum["daily"].replace(0, 0.001)
    ).round(0).clip(upper=999)
    cluster_sum = cluster_sum.sort_values("days")
    cluster_priority = cluster_sum.rename(columns={
        "cluster": "Кластер", "stock_total": "Остаток", "sold": "Продажи",
        "daily": "Прод/день", "days": "Дней запаса", "order": "Заказать",
    })

    print(f"  План готов: {len(df_plan)} строк, заказать {int(df_plan['order'].sum())} шт "
          f"(источник: {'ЛК-файл' if source == 'lk' else 'API goods-turnover'})")

    return {
        "cluster_priority": cluster_priority,
        "plan": df_plan,
        "source": source,
    }
