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
    if creds:
        return creds
    from config import WB_API_KEY
    return {"WB_API_KEY": WB_API_KEY}


def _wb_headers(creds):
    c = _get_creds(creds)
    return {
        "Authorization": c["WB_API_KEY"],
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


def _wh_cluster(name: str) -> str:
    """Маппинг склада WB в кластер. Fuzzy-fallback по первому слову."""
    if name in WB_WH_TO_CLUSTER:
        return WB_WH_TO_CLUSTER[name]
    first = name.split()[0] if name else ''
    for k, v in WB_WH_TO_CLUSTER.items():
        if k.startswith(first):
            return v
    return 'Прочее'


# ── Загрузка квантов ──────────────────────────────────────────────────────

def load_quants(path: str = QUANTUM_FILE) -> pd.DataFrame:
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

def compute_wb_supply_data(days_sales: int = DAYS_SALES, days_plan: int = DAYS_PLAN,
                           quantum_file: str = QUANTUM_FILE):
    """Вычислить план поставок WB для дашборда.

    Returns dict:
      cluster_priority: DataFrame (Кластер, Остаток, Продажи, Прод/день, Дней запаса, Заказать)
      plan: DataFrame (full plan by article x cluster)
    """
    # Кванты и цены
    df_q = load_quants(quantum_file)
    quants = dict(zip(df_q['Артикул'], df_q['квант']))
    prices = dict(zip(df_q['Артикул'], df_q['Цена']))
    names = dict(zip(df_q['Артикул'], df_q['Название']))

    # Остатки
    stocks = get_wb_stocks()
    if stocks.empty:
        return None

    # Агрегируем остатки по артикулу + кластер
    stock_cl = stocks.groupby(['supplierArticle', 'cluster']).agg(
        stock=('quantity', 'sum'),
    ).reset_index()
    stock_cl.rename(columns={'supplierArticle': 'article'}, inplace=True)

    # Продажи
    sales = get_wb_sales_by_warehouse(days_sales)

    if not sales.empty:
        sales_cl = sales.groupby(['article', 'cluster']).agg(
            sold=('sold', 'sum'),
        ).reset_index()
        sales_cl['daily'] = (sales_cl['sold'] / days_sales).round(2)
    else:
        sales_cl = pd.DataFrame(columns=['article', 'cluster', 'sold', 'daily'])

    # Объединяем
    df_plan = stock_cl.merge(
        sales_cl[['article', 'cluster', 'sold', 'daily']],
        on=['article', 'cluster'], how='outer',
    ).fillna(0)

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

    # Приоритет кластеров
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
