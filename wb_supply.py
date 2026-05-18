"""
Планирование поставок Wildberries.

Остатки — WB Statistics API (supplier/stocks).
Продажи по складам — reportDetailByPeriod (office_name).
Кванты — quantum_stock.xlsx (матч по Артикул = supplierArticle).

Основные функции:
  - get_wb_stocks()            — остатки по складам
  - get_wb_sales_by_warehouse() — продажи по складам за N дней
  - get_wb_stock_turnover()    — оборачиваемость SKU x склад
  - build_wb_supply_plan()     — полный план поставок
  - compute_wb_supply_data()   — данные для дашборда (без Excel)
"""

import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta

from utils.price_loader import load_price

# ── Настройки ────────────────────────────────────────────────────────────

DAYS_SALES = 30
DAYS_PLAN = 60
QUANTUM_FILE = "quantum_stock.xlsx"


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
        "Authorization": c.get("WB_API_KEY", ""),
        "Content-Type": "application/json",
    }

STATS_BASE = "https://statistics-api.wildberries.ru"

# ── Маппинг складов WB → кластеры ────────────────────────────────────────

WB_WH_TO_CLUSTER = {
    # Москва МО
    'Электросталь': 'Москва МО', 'Подольск': 'Москва МО', 'Подольск 3': 'Москва МО',
    'Подольск 4': 'Москва МО', 'Коледино': 'Москва МО', 'Тула': 'Москва МО',
    'Белые Столбы': 'Москва МО', 'Чехов': 'Москва МО', 'Чехов 2': 'Москва МО',
    'Домодедово': 'Москва МО',
    # СПб
    'Санкт-Петербург': 'СПб СЗО', 'СПб Уткина Заводь': 'СПб СЗО',
    'Невский': 'СПб СЗО',
    # Регионы
    'Казань': 'Казань', 'Екатеринбург': 'Екатеринбург', 'Екатеринбург 2': 'Екатеринбург',
    'Новосибирск': 'Новосибирск', 'Краснодар': 'Краснодар',
    'Ростов-на-Дону': 'Ростов', 'Хабаровск': 'Дальний Восток', 'Хабаровск 2': 'Дальний Восток',
    'Красноярск': 'Красноярск', 'Волгоград': 'Саратов',
    'Самара': 'Самара', 'Нижний Новгород': 'Казань',
    'Воронеж': 'Воронеж', 'Пермь': 'Пермь',
    'Минск': 'Беларусь', 'Астана': 'Астана', 'Алматы': 'Алматы',
}

# Кластер → федеральный округ (для группировки в плане поставок)
WB_CLUSTER_TO_DISTRICT = {
    'Москва МО': 'ЦФО',
    'Воронеж': 'ЦФО',
    'СПб СЗО': 'СЗФО',
    'Калининград': 'СЗФО',
    'Казань': 'ПФО',
    'Самара': 'ПФО',
    'Пермь': 'ПФО',
    'Саратов': 'ПФО',
    'Екатеринбург': 'УФО',
    'Тюмень': 'УФО',
    'Новосибирск': 'СФО',
    'Красноярск': 'СФО',
    'Омск': 'СФО',
    'Краснодар': 'ЮФО',
    'Ростов': 'ЮФО',
    'Невинномысск': 'СКФО',
    'Махачкала': 'СКФО',
    'Дальний Восток': 'ДФО',
    'Беларусь': 'Беларусь',
    'Астана': 'Казахстан',
    'Алматы': 'Казахстан',
    'Казахстан': 'Казахстан',
    'Армения': 'Армения',
    'Кыргызстан': 'Кыргызстан',
    'Грузия': 'Грузия',
    'Азербайджан': 'Азербайджан',
}


def _wh_cluster(name: str) -> str:
    """Маппинг склада WB в кластер. Fuzzy-fallback по первому слову."""
    if name in WB_WH_TO_CLUSTER:
        return WB_WH_TO_CLUSTER[name]
    first = name.split()[0] if name else ''
    for k, v in WB_WH_TO_CLUSTER.items():
        if k.startswith(first):
            return v
    return 'Прочее'


def _district(cluster: str) -> str:
    """Кластер → федеральный округ."""
    return WB_CLUSTER_TO_DISTRICT.get(cluster, 'Прочее')


# ── Загрузка квантов ──────────────────────────────────────────────────────

def load_quants(path: str = QUANTUM_FILE) -> pd.DataFrame:
    from pathlib import Path
    if not Path(path).exists():
        print(f"  Файл квантов не найден: {path} — используются значения по умолчанию")
        return pd.DataFrame(columns=['Артикул', 'квант', 'Цена', 'Название'])
    df = pd.read_excel(path)
    df['Артикул'] = df['Артикул'].astype(str).str.strip()
    return df


# ── Остатки ───────────────────────────────────────────────────────────────

def get_wb_stocks(creds=None) -> pd.DataFrame:
    """Остатки по складам WB (supplier/stocks)."""
    headers = _wb_headers(creds)
    resp = requests.get(
        f"{STATS_BASE}/api/v1/supplier/stocks",
        headers=headers,
        params={"dateFrom": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")},
    )
    if resp.status_code != 200:
        print(f"Ошибка остатков WB: {resp.status_code}")
        return pd.DataFrame()

    data = resp.json()
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df['cluster'] = df['warehouseName'].apply(_wh_cluster)
    df['supplierArticle'] = df['supplierArticle'].astype(str).str.strip()
    print(f"  Остатки WB: {len(df)} записей, {df['nmId'].nunique()} nmID")
    return df


# ── Продажи по складам ───────────────────────────────────────────────────

def get_wb_sales_by_warehouse(days: int = DAYS_SALES, creds=None) -> pd.DataFrame:
    """Продажи по складам за N дней из reportDetailByPeriod."""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    headers = _wb_headers(creds)
    url = f"{STATS_BASE}/api/v5/supplier/reportDetailByPeriod"
    all_data = []
    rrdid = 0
    page = 1

    while True:
        params = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": 100000,
            "rrdid": rrdid,
        }
        resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 429:
            print("  Rate limit, жду 65с...")
            time.sleep(65)
            resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 204 or not resp.content:
            break
        if resp.status_code != 200:
            print(f"  Ошибка: {resp.status_code}")
            break

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

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    # Только продажи (quantity > 0) — возвраты (quantity < 0) вычитаем
    df['quantity'] = pd.to_numeric(df.get('quantity', 0), errors='coerce').fillna(0)
    df['sa_name'] = df['sa_name'].astype(str).str.strip()
    df['office_name'] = df['office_name'].astype(str).str.strip()

    # Агрегируем: продажи по артикулу + склад
    sales = df[df['supplier_oper_name'] == 'Продажа'].groupby(
        ['sa_name', 'nm_id', 'office_name']
    ).agg({'quantity': 'sum'}).reset_index()
    sales.columns = ['article', 'nm_id', 'warehouse', 'sold']
    sales['cluster'] = sales['warehouse'].apply(_wh_cluster)

    print(f"  Продажи WB: {len(sales)} строк, {sales['article'].nunique()} артикулов")
    return sales


# ── Оборачиваемость ──────────────────────────────────────────────────────

def get_wb_stock_turnover(days: int = DAYS_SALES) -> pd.DataFrame:
    """Объединяет остатки и продажи → оборачиваемость по SKU x кластер."""
    stocks = get_wb_stocks()
    sales = get_wb_sales_by_warehouse(days)

    if stocks.empty:
        return pd.DataFrame()

    # Остатки: группируем по артикулу + кластер
    stock_cl = stocks.groupby(['supplierArticle', 'cluster']).agg(
        stock=('quantity', 'sum'),
        inWayToClient=('inWayToClient', 'sum'),
        inWayFromClient=('inWayFromClient', 'sum'),
    ).reset_index()
    stock_cl.rename(columns={'supplierArticle': 'article'}, inplace=True)

    if sales.empty:
        stock_cl['sold'] = 0
        stock_cl['daily'] = 0.0
        stock_cl['days'] = 999
        return stock_cl

    sales_cl = sales.groupby(['article', 'cluster']).agg(
        sold=('sold', 'sum'),
    ).reset_index()
    sales_cl['daily'] = (sales_cl['sold'] / days).round(2)

    result = stock_cl.merge(sales_cl, on=['article', 'cluster'], how='outer').fillna(0)
    result['days'] = (
        result['stock'] / result['daily'].replace(0, 0.0001)
    ).round(0).clip(upper=999)
    result.loc[result['daily'] == 0, 'days'] = 999

    return result


# ── План поставок ─────────────────────────────────────────────────────────

def _sales_from_cache(user_id=None):
    """Достать продажи по складам из кэша wb_df_m, без API-вызова.

    Тот же reportDetailByPeriod уже скачан для расчёта маржи (`wb_df_m`),
    нет смысла его повторно дёргать здесь. Возвращает DataFrame
    с колонками [article, cluster, sold] либо пустой.
    """
    try:
        from data_loader import load as _ld
    except Exception:
        return pd.DataFrame()
    df = _ld('wb_df_m', user_id)
    if df is None or df.empty or 'supplier_oper_name' not in df.columns:
        return pd.DataFrame()
    sales = df[df['supplier_oper_name'] == 'Продажа'].copy()
    if sales.empty:
        return pd.DataFrame()
    sales['sa_name'] = sales['sa_name'].astype(str).str.strip()
    sales['office_name'] = sales['office_name'].astype(str).str.strip()
    sales['cluster'] = sales['office_name'].apply(_wh_cluster)
    out = sales.groupby(['sa_name', 'cluster'])['quantity'].sum().reset_index()
    out.columns = ['article', 'cluster', 'sold']
    return out


def compute_wb_supply_data(days_sales: int = DAYS_SALES, days_plan: int = DAYS_PLAN,
                           quantum_file: str = QUANTUM_FILE, creds=None, user_id=None):
    """Вычислить план поставок WB для дашборда.

    Returns dict:
      cluster_priority: DataFrame (Кластер, Остаток, Продажи, Прод/день, Дней запаса, Заказать)
      plan: DataFrame (full plan by article x cluster)
    """
    # Кванты и цены
    df_q = load_quants(quantum_file)
    quants = dict(zip(df_q['Артикул'], df_q['квант'])) if not df_q.empty else {}
    prices = dict(zip(df_q['Артикул'], df_q['Цена'])) if not df_q.empty else {}
    names = dict(zip(df_q['Артикул'], df_q['Название'])) if not df_q.empty else {}

    # Остатки: пытаемся свежие, при 429 — фоллбэк на кэш wb_stock_turnover
    stocks = get_wb_stocks(creds=creds)
    if stocks.empty:
        print("  → пробуем кэш wb_stock_turnover")
        try:
            from data_loader import load as _ld
            cached_t = _ld('wb_stock_turnover', user_id)
            if cached_t is not None and not cached_t.empty:
                stocks = cached_t.copy()
                stocks.rename(columns={'article': 'supplierArticle'}, inplace=True)
                if 'cluster' not in stocks.columns and 'warehouseName' in stocks.columns:
                    stocks['cluster'] = stocks['warehouseName'].apply(_wh_cluster)
                if 'quantity' not in stocks.columns and 'stock' in stocks.columns:
                    stocks['quantity'] = stocks['stock']
                print(f"  кэш wb_stock_turnover: {len(stocks)} строк")
        except Exception as e:
            print(f"  кэш fallback упал: {e}")
    if stocks.empty:
        print("  Остатки WB недоступны и кэша нет — план не построить")
        return None

    # Агрегируем остатки по артикулу + кластер
    stock_cl = stocks.groupby(['supplierArticle', 'cluster']).agg(
        stock=('quantity', 'sum'),
    ).reset_index()
    stock_cl.rename(columns={'supplierArticle': 'article'}, inplace=True)

    # Продажи: берём из уже скачанного wb_df_m (для маржи), без второго API-вызова
    sales_cl_df = _sales_from_cache(user_id)
    if sales_cl_df.empty:
        # Кэш пуст — последняя попытка через API
        print("  wb_df_m пуст, пробуем API")
        api_sales = get_wb_sales_by_warehouse(days_sales, creds=creds)
        if not api_sales.empty:
            sales_cl_df = api_sales.groupby(['article', 'cluster'])['sold'].sum().reset_index()
    if not sales_cl_df.empty:
        sales_cl = sales_cl_df.copy()
        sales_cl['daily'] = (sales_cl['sold'] / days_sales).round(2)
        print(f"  Продажи WB: {len(sales_cl)} строк (из кэша wb_df_m)")
    else:
        sales_cl = pd.DataFrame(columns=['article', 'cluster', 'sold', 'daily'])

    # Объединяем
    df_plan = stock_cl.merge(
        sales_cl[['article', 'cluster', 'sold', 'daily']],
        on=['article', 'cluster'], how='outer',
    ).fillna(0)

    # Маппим кластер → федеральный округ и переагрегируем по (article × district)
    df_plan['district'] = df_plan['cluster'].apply(_district)
    df_plan = df_plan.groupby(['article', 'district'], as_index=False).agg({
        'stock': 'sum', 'sold': 'sum', 'daily': 'sum',
    })

    # Названия из квантов
    df_plan['name'] = df_plan['article'].map(names).fillna('')

    # Цены и кванты
    df_plan['price'] = df_plan['article'].map(prices)
    df_plan['quant'] = df_plan['article'].map(quants).fillna(1).astype(int)

    # Расчёт
    df_plan['days'] = (
        df_plan['stock'] / df_plan['daily'].replace(0, 0.0001)
    ).round(0).clip(upper=999)
    df_plan.loc[df_plan['daily'] == 0, 'days'] = 999

    df_plan['need'] = (
        (df_plan['daily'] * days_plan) - df_plan['stock']
    ).clip(lower=0).round(0)

    df_plan['boxes'] = np.ceil(df_plan['need'] / df_plan['quant'].replace(0, 1)).astype(int)
    df_plan.loc[(df_plan['need'] > 0) & (df_plan['boxes'] == 0), 'boxes'] = 1
    df_plan.loc[
        (df_plan['stock'] == 0) & (df_plan['sold'] > 0) & (df_plan['boxes'] == 0),
        'boxes'
    ] = 1
    df_plan['order'] = df_plan['boxes'] * df_plan['quant']

    # Для совместимости с UI оставляем поле 'cluster' = district
    df_plan['cluster'] = df_plan['district']

    # Приоритет округов
    cluster_sum = df_plan.groupby('cluster').agg({
        'stock': 'sum', 'sold': 'sum', 'daily': 'sum', 'order': 'sum',
    }).reset_index()
    cluster_sum['days'] = (
        cluster_sum['stock'] / cluster_sum['daily'].replace(0, 0.001)
    ).round(0).clip(upper=999)
    cluster_sum = cluster_sum.sort_values('days')
    cluster_priority = cluster_sum.rename(columns={
        'cluster': 'Кластер', 'stock': 'Остаток', 'sold': 'Продажи',
        'daily': 'Прод/день', 'days': 'Дней запаса', 'order': 'Заказать',
    })

    return {
        'cluster_priority': cluster_priority,
        'plan': df_plan,
    }
