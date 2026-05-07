"""
Планирование поставок Ozon.

Порт ноутбука Планирование_поставок_Озон_12_02.ipynb.
Ключи из config.py, кванты из quantum_stock.xlsx.

Основные функции:
  - get_stock_turnover()  — DataFrame (SKU x склад) с оборачиваемостью
  - build_supply_plan()   — полный план поставок, сохраняет Excel
  - main()                — запуск из CLI
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

# ── Настройки ────────────────────────────────────────────────────────────

DAYS_SALES = 30   # за сколько дней берём продажи
DAYS_PLAN  = 60   # на сколько дней планируем запас
QUANTUM_FILE = "quantum_stock.xlsx"

LIQUIDITY_DEFICIT_DAYS = 14   # дней запаса меньше — Дефицит
LIQUIDITY_OVERSTOCK_DAYS = 60  # дней запаса больше — Избыточный


def _liquidity_label(days, sold=None):
    """Метка ликвидности по дням запаса (а-ля Ozon)."""
    if sold is not None and sold == 0:
        return 'Нет продаж'
    if days is None:
        return ''
    try:
        d = float(days)
    except (TypeError, ValueError):
        return ''
    if d < LIQUIDITY_DEFICIT_DAYS:
        return 'Дефицит'
    if d > LIQUIDITY_OVERSTOCK_DAYS:
        return 'Избыточный'
    return 'Норма'


def _get_creds(creds):
    if creds is not None:
        return creds
    try:
        from config import OZON_SELLER_CLIENT_ID, OZON_SELLER_API_KEY_V2
        return {
            "OZON_SELLER_CLIENT_ID": OZON_SELLER_CLIENT_ID,
            "OZON_SELLER_API_KEY_V2": OZON_SELLER_API_KEY_V2,
        }
    except ImportError:
        return {}


def _ozon_headers(creds):
    c = _get_creds(creds)
    return {
        "Client-Id": c.get("OZON_SELLER_CLIENT_ID", ""),
        "Api-Key": c.get("OZON_SELLER_API_KEY_V2", c.get("OZON_SELLER_API_KEY", "")),
        "Content-Type": "application/json",
    }

# ── Маппинг складов → кластеры ───────────────────────────────────────────

WH_TO_CLUSTER = {
    'ПЕТРОВСКОЕ_РФЦ': 'Москва МО', 'ХОРУГВИНО_РФЦ': 'Москва МО', 'НОГИНСК_РФЦ': 'Москва МО',
    'ПУШКИНО_1_РФЦ': 'Москва МО', 'ПУШКИНО_2_РФЦ': 'Москва МО', 'ГРИВНО_РФЦ': 'Москва МО',
    'ДОМОДЕДОВО_РФЦ': 'Москва МО', 'СОФЬИНО_РФЦ': 'Москва МО', 'ЖУКОВСКИЙ_РФЦ': 'Москва МО',
    'СПБ_КОЛПИНО_РФЦ': 'СПб СЗО', 'СПБ_БУГРЫ_РФЦ': 'СПб СЗО', 'СПБ_ШУШАРЫ_РФЦ': 'СПб СЗО',
    'Санкт_Петербург_РФЦ': 'СПб СЗО', 'САНКТ-ПЕТЕРБУРГ_РФЦ': 'СПб СЗО',
    'КАЛИНИНГРАД_МРФЦ': 'Калининград', 'ЯРОСЛАВЛЬ_РФЦ': 'Ярославль', 'ТВЕРЬ_РФЦ': 'Тверь',
    'ВОРОНЕЖ_2_РФЦ': 'Воронеж', 'ВОРОНЕЖ_МРФЦ': 'Воронеж',
    'Ростов_на_Дону_РФЦ': 'Ростов', 'РОСТОВ-НА-ДОНУ_РФЦ': 'Ростов', 'РОСТОВ_НА_ДОНУ_2_РФЦ': 'Ростов',
    'АДЫГЕЙСК_РФЦ': 'Краснодар', 'КРАСНОДАР_2_РФЦ': 'Краснодар', 'НОВОРОССИЙСК_МРФЦ': 'Краснодар',
    'НЕВИННОМЫССК_РФЦ': 'Невинномысск', 'МАХАЧКАЛА_РФЦ': 'Махачкала',
    'Казань_РФЦ_НОВЫЙ': 'Казань', 'КАЗАНЬ_РФЦ_НОВЫЙ': 'Казань', 'НИЖНИЙ_НОВГОРОД_РФЦ': 'Казань',
    'САМАРА_РФЦ': 'Самара', 'САРАТОВ_РФЦ': 'Саратов', 'ВОЛГОГРАД_МРФЦ': 'Саратов',
    'Екатеринбург_РФЦ_НОВЫЙ': 'Екатеринбург', 'ЕКАТЕРИНБУРГ_РФЦ_НОВЫЙ': 'Екатеринбург',
    'ПЕРМЬ_РФЦ': 'Пермь', 'УФА_РФЦ': 'Уфа', 'ОРЕНБУРГ_РФЦ': 'Оренбург', 'ТЮМЕНЬ_РФЦ': 'Тюмень',
    'Новосибирск_РФЦ_НОВЫЙ': 'Новосибирск', 'НОВОСИБИРСК_РФЦ_НОВЫЙ': 'Новосибирск',
    'ОМСК_РФЦ': 'Омск', 'КРАСНОЯРСК_МРФЦ': 'Красноярск', 'ХАБАРОВСК_2_РФЦ': 'Дальний Восток',
    'МИНСК_МПСЦ': 'Беларусь', 'АСТАНА_РФЦ': 'Казахстан', 'АЛМАТЫ_2_РФЦ': 'Казахстан',
}


# Маппинг cluster_from/cluster_to (API) → наши короткие названия
API_CLUSTER_NORMALIZE = {
    'Москва, МО и Дальние регионы': 'Москва МО',
    'Санкт-Петербург и СЗО': 'СПб СЗО',
    'Астана': 'Казахстан',
    'Алматы': 'Казахстан',
}


# ── API: загрузка остатков ───────────────────────────────────────────────

def fetch_stocks(creds=None) -> list:
    headers = _ozon_headers(creds)
    all_stocks = []
    offset = 0
    while True:
        resp = requests.post(
            "https://api-seller.ozon.ru/v2/analytics/stock_on_warehouses",
            headers=headers, json={"limit": 1000, "offset": offset},
        )
        if resp.status_code != 200:
            print(f"  Ошибка остатков: {resp.status_code}")
            break
        rows = resp.json().get("result", {}).get("rows", [])
        if not rows:
            break
        all_stocks.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    print(f"  Остатков: {len(all_stocks)}")
    return all_stocks


# ── API: загрузка продаж (FBO postings) ──────────────────────────────────

def fetch_postings(days: int = DAYS_SALES, creds=None) -> list:
    headers = _ozon_headers(creds)
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    date_to = datetime.now().strftime("%Y-%m-%dT23:59:59.000Z")

    all_postings = []
    offset = 0
    while True:
        resp = requests.post(
            "https://api-seller.ozon.ru/v2/posting/fbo/list",
            headers=headers,
            json={
                "dir": "DESC",
                "filter": {"since": date_from, "to": date_to},
                "limit": 1000, "offset": offset,
                "with": {"analytics_data": True, "financial_data": True},
            },
        )
        if resp.status_code != 200:
            print(f"  Ошибка отправлений: {resp.status_code}")
            break
        result = resp.json().get("result", [])
        postings = result if isinstance(result, list) else result.get("postings", [])
        if not postings:
            break
        all_postings.extend(postings)
        if len(all_postings) % 5000 < 1000:
            print(f"   {len(all_postings)}")
        if len(postings) < 1000:
            break
        offset += 1000
    print(f"  Отправлений: {len(all_postings)}")
    return all_postings


# ── Загрузка файла квантов ───────────────────────────────────────────────

def load_quants(path: str = QUANTUM_FILE) -> pd.DataFrame:
    from pathlib import Path
    if not Path(path).exists():
        print(f"  Файл квантов не найден: {path} — используются значения по умолчанию")
        return pd.DataFrame(columns=['SKU', 'квант', 'Цена'])
    df = pd.read_excel(path)
    df['SKU'] = df['SKU'].astype(str).str.replace('.0', '', regex=False)
    print(f"  Квантов: {len(df)}")
    return df


# ── Обработка данных ─────────────────────────────────────────────────────

def process_stocks(raw_stocks: list) -> pd.DataFrame:
    if not raw_stocks:
        return pd.DataFrame(columns=['sku', 'item_name', 'warehouse_name',
                                     'free_to_sell_amount', 'promised_amount', 'cluster'])
    df = pd.DataFrame(raw_stocks)
    if 'warehouse_name' not in df.columns:
        print(f"  Неожиданные колонки остатков: {list(df.columns)}")
        return pd.DataFrame(columns=['sku', 'item_name', 'warehouse_name',
                                     'free_to_sell_amount', 'promised_amount', 'cluster'])
    df = df[~df['warehouse_name'].str.contains('FRESH', case=False, na=False)]
    df = df[~df['item_name'].str.lower().str.contains('уцен', na=False)]
    df['cluster'] = df['warehouse_name'].map(WH_TO_CLUSTER).fillna('Прочее')
    return df


def _normalize_cluster(name: str) -> str:
    """Normalize API cluster name to our short name."""
    if not name:
        return 'Прочее'
    return API_CLUSTER_NORMALIZE.get(name, name)


def process_sales(raw_postings: list) -> pd.DataFrame:
    sales_data = []
    for p in raw_postings:
        analytics = p.get('analytics_data', {}) or {}
        financial = p.get('financial_data', {}) or {}
        warehouse = analytics.get('warehouse_name', '')
        if 'FRESH' in warehouse:
            continue

        cluster_from = financial.get('cluster_from', '')
        cluster_to = financial.get('cluster_to', '')

        # Дата создания отправления — для метрики "дней без продаж"
        sale_dt_raw = p.get('in_process_at') or p.get('created_at') or ''
        try:
            sale_date = pd.to_datetime(sale_dt_raw).date() if sale_dt_raw else None
        except Exception:
            sale_date = None

        for product in p.get('products', []):
            name = product.get('name', '')
            if 'уцен' in name.lower():
                continue
            sales_data.append({
                'sku': product.get('sku'),
                'name': name,
                'warehouse': warehouse,
                'cluster_from_raw': cluster_from,
                'cluster_to_raw': cluster_to,
                'quantity': product.get('quantity', 1),
                'sale_date': sale_date,
            })
    df = pd.DataFrame(sales_data)
    if df.empty:
        return df

    # Use API cluster data directly, normalize long names
    df['wh_cluster'] = df['cluster_from_raw'].apply(_normalize_cluster)
    df['dest_cluster'] = df['cluster_to_raw'].apply(_normalize_cluster)

    # Fallback to warehouse mapping if API cluster is empty
    mask_no_from = df['wh_cluster'] == 'Прочее'
    if mask_no_from.any():
        df.loc[mask_no_from, 'wh_cluster'] = (
            df.loc[mask_no_from, 'warehouse'].map(WH_TO_CLUSTER).fillna('Прочее')
        )
    mask_no_to = df['dest_cluster'] == 'Прочее'
    if mask_no_to.any():
        df.loc[mask_no_to, 'dest_cluster'] = df.loc[mask_no_to, 'wh_cluster']

    return df


# ══════════════════════════════════════════════════════════════════════════
# get_stock_turnover — DataFrame для дашборда
# ══════════════════════════════════════════════════════════════════════════

def get_stock_turnover(
    days_sales: int = DAYS_SALES,
    creds=None,
) -> pd.DataFrame:
    """
    Возвращает DataFrame с оборачиваемостью по каждому SKU на каждом складе.

    Колонки:
      sku, name, cluster, warehouse, stock, daily_sales, days_of_stock

    Каждая строка = SKU на конкретном складе.
    cluster — группировка для свёртки.
    """
    print("Загрузка остатков...")
    raw_stocks = fetch_stocks(creds)
    df_stock = process_stocks(raw_stocks)

    print(f"Загрузка продаж за {days_sales} дней...")
    raw_postings = fetch_postings(days_sales, creds)
    df_sales = process_sales(raw_postings)

    # Остатки по складам
    stock_wh = df_stock.groupby(
        ['sku', 'item_name', 'warehouse_name', 'cluster']
    ).agg({
        'free_to_sell_amount': 'sum',
        'reserved_amount': 'sum',
    }).reset_index()
    stock_wh['stock'] = stock_wh['free_to_sell_amount'] + stock_wh['reserved_amount']
    stock_wh.rename(columns={
        'item_name': 'name',
        'warehouse_name': 'warehouse',
    }, inplace=True)

    # Продажи по кластерам (dest_cluster — куда реальный спрос)
    if not df_sales.empty:
        sales_wh = (
            df_sales.groupby(['sku', 'dest_cluster'])['quantity']
            .sum().reset_index()
        )
        sales_wh.columns = ['sku', 'cluster', 'sold']
        sales_wh['daily_sales'] = (sales_wh['sold'] / days_sales).round(2)
    else:
        sales_wh = pd.DataFrame(columns=['sku', 'cluster', 'sold', 'daily_sales'])

    # Merge
    result = stock_wh.merge(
        sales_wh[['sku', 'cluster', 'daily_sales']],
        on=['sku', 'cluster'],
        how='left',
    )
    result['daily_sales'] = result['daily_sales'].fillna(0)
    result['days_of_stock'] = (
        result['stock'] / result['daily_sales'].replace(0, 0.0001)
    ).round(0).clip(upper=999).astype(int)
    result.loc[result['daily_sales'] == 0, 'days_of_stock'] = 999

    result = result[['sku', 'name', 'cluster', 'warehouse', 'stock',
                      'daily_sales', 'days_of_stock']]
    result = result.sort_values(['cluster', 'sku', 'warehouse']).reset_index(drop=True)

    print(f"  Итого строк: {len(result)}, SKU: {result['sku'].nunique()}, "
          f"складов: {result['warehouse'].nunique()}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# build_supply_plan — полный план поставок (Excel)
# ══════════════════════════════════════════════════════════════════════════

def build_supply_plan(
    days_sales: int = DAYS_SALES,
    days_plan: int = DAYS_PLAN,
    quantum_file: str = QUANTUM_FILE,
    output: str | None = None,
    creds=None,
    user_id=None,
) -> str:
    """Строит план поставок и сохраняет Excel. Возвращает имя файла."""

    if output is None:
        output = f"supply_plan_{days_sales}d.xlsx"

    # Кванты
    print("Загрузка квантов...")
    df_q = load_quants(quantum_file)
    quants = dict(zip(df_q['SKU'], df_q['квант']))
    barcodes = dict(zip(df_q['SKU'], df_q.get('штрихкод', pd.Series(dtype=str))))
    prices = dict(zip(df_q['SKU'], df_q.get('Цена', pd.Series(dtype=float))))
    stock_italco = dict(zip(df_q['SKU'], df_q.get('сток италко', pd.Series(dtype=float))))
    name_1c = dict(zip(df_q['SKU'], df_q.get('Наименование 1С', pd.Series(dtype=str))))

    # Остатки
    print("Загрузка остатков...")
    raw_stocks = fetch_stocks(creds=creds)
    df_stock = process_stocks(raw_stocks)

    # Продажи
    print(f"Загрузка продаж за {days_sales} дней...")
    raw_postings = fetch_postings(days_sales, creds=creds)
    df_sales = process_sales(raw_postings)

    print("Обработка данных...")

    # Остатки по складам (для вкладки)
    stock_by_wh = df_stock.groupby('warehouse_name').agg({
        'free_to_sell_amount': 'sum',
        'reserved_amount': 'sum',
        'promised_amount': 'sum',
    }).reset_index()
    stock_by_wh.columns = ['Склад', 'Свободно', 'В заказах', 'В пути']
    stock_by_wh['Всего'] = stock_by_wh['Свободно'] + stock_by_wh['В заказах']
    stock_by_wh = stock_by_wh.sort_values('Всего', ascending=False)

    # Остатки по кластерам
    stock_cluster = df_stock.groupby(['sku', 'item_name', 'cluster']).agg({
        'free_to_sell_amount': 'sum', 'promised_amount': 'sum',
    }).reset_index()
    stock_cluster.columns = ['sku', 'name', 'cluster', 'stock', 'in_transit']
    stock_cluster['stock_total'] = stock_cluster['stock']  # только свободный остаток (без in_transit к покупателю)

    # Продажи по кластерам
    if not df_sales.empty:
        sales_cluster = df_sales.groupby(
            ['sku', 'name', 'dest_cluster']
        ).agg({'quantity': 'sum'}).reset_index()
        sales_cluster.columns = ['sku', 'name', 'cluster', 'sold']
        sales_cluster['daily'] = (sales_cluster['sold'] / days_sales).round(2)
    else:
        sales_cluster = pd.DataFrame(columns=['sku', 'name', 'cluster', 'sold', 'daily'])

    # План
    df_plan = stock_cluster.merge(
        sales_cluster[['sku', 'cluster', 'sold', 'daily']],
        on=['sku', 'cluster'], how='outer',
    ).fillna(0)
    df_plan['name'] = df_plan['name'].replace(0, '')
    if not sales_cluster.empty:
        name_map = sales_cluster.set_index('sku')['name'].to_dict()
        df_plan['name'] = df_plan.apply(
            lambda r: r['name'] if r['name'] else name_map.get(r['sku'], ''), axis=1)

    df_plan['sku_str'] = df_plan['sku'].astype(str).str.replace('.0', '', regex=False)
    df_plan['barcode'] = df_plan['sku_str'].map(barcodes)
    df_plan['price'] = df_plan['sku_str'].map(prices)
    df_plan['stock_italco'] = df_plan['sku_str'].map(stock_italco)
    df_plan['name_1c'] = df_plan['sku_str'].map(name_1c)
    df_plan['quant'] = df_plan['sku_str'].map(quants).fillna(6).astype(int)

    # Map SKU → article from catalog DB
    try:
        from db import get_catalog_sku_map
        sku_to_art = get_catalog_sku_map(user_id=user_id)
        df_plan['article'] = df_plan['sku_str'].map(sku_to_art).fillna('')
    except Exception:
        df_plan['article'] = ''

    df_plan['days'] = (
        df_plan['stock_total'] / df_plan['daily'].replace(0, 0.0001)
    ).round(0).clip(upper=999)
    df_plan.loc[df_plan['daily'] == 0, 'days'] = 999
    df_plan['need'] = (
        (df_plan['daily'] * days_plan) - df_plan['stock'] - df_plan['in_transit']
    ).clip(lower=0).round(0)
    df_plan['boxes'] = np.ceil(df_plan['need'] / df_plan['quant']).astype(int)
    df_plan.loc[(df_plan['need'] > 0) & (df_plan['boxes'] == 0), 'boxes'] = 1
    df_plan.loc[
        (df_plan['stock_total'] == 0) & (df_plan['sold'] > 0) & (df_plan['boxes'] == 0),
        'boxes'
    ] = 1
    df_plan['order'] = df_plan['boxes'] * df_plan['quant']

    # Магистраль
    if not df_sales.empty:
        outflow = df_sales.groupby('wh_cluster')['quantity'].sum().reset_index()
        outflow.columns = ['Кластер', 'Убытие']
        demand = df_sales.groupby('dest_cluster')['quantity'].sum().reset_index()
        demand.columns = ['Кластер', 'Спрос']
        df_mag = outflow.merge(demand, on='Кластер', how='outer').fillna(0)
        df_mag['Убытие/д'] = (df_mag['Убытие'] / days_sales).round(1)
        df_mag['Спрос/д'] = (df_mag['Спрос'] / days_sales).round(1)
        df_mag['%'] = (
            (df_mag['Убытие/д'] / df_mag['Спрос/д'].replace(0, 0.001) - 1) * 100
        ).round(0)
        df_mag['Статус'] = df_mag['%'].apply(
            lambda x: 'Хомяк' if x > 20 else ('Вампир' if x < -20 else 'Баланс'))
        df_mag = df_mag.sort_values('%', ascending=False)

        red_clusters = df_mag[df_mag['%'] > 20]['Кластер'].tolist()
        magistral_detail = df_sales[
            (df_sales['wh_cluster'].isin(red_clusters))
            & (df_sales['wh_cluster'] != df_sales['dest_cluster'])
        ].groupby(['sku', 'name', 'wh_cluster', 'dest_cluster'])['quantity'].sum().reset_index()
        magistral_detail.columns = ['SKU', 'Название', 'Откуда', 'Куда', 'Кол-во']
        magistral_detail = magistral_detail.sort_values('Кол-во', ascending=False)
    else:
        df_mag = pd.DataFrame()
        magistral_detail = pd.DataFrame()
        red_clusters = []

    # ── Дни без продаж (для каждой пары sku × cluster) ──
    from datetime import date as _date
    today = _date.today()
    if not df_sales.empty and 'sale_date' in df_sales.columns:
        last_sale_cluster = (
            df_sales.dropna(subset=['sale_date'])
            .groupby(['sku', 'dest_cluster'])['sale_date'].max()
            .reset_index()
        )
        last_sale_cluster.columns = ['sku', 'cluster', 'last_sale']
        last_sale_sku = (
            df_sales.dropna(subset=['sale_date'])
            .groupby('sku')['sale_date'].max()
            .reset_index()
        )
        last_sale_sku.columns = ['sku', 'last_sale_sku']
    else:
        last_sale_cluster = pd.DataFrame(columns=['sku', 'cluster', 'last_sale'])
        last_sale_sku = pd.DataFrame(columns=['sku', 'last_sale_sku'])

    df_plan = df_plan.merge(last_sale_cluster, on=['sku', 'cluster'], how='left')
    df_plan['days_no_sale'] = df_plan['last_sale'].apply(
        lambda d: (today - d).days if pd.notna(d) else days_sales
    )
    df_plan['liquidity'] = df_plan.apply(
        lambda r: _liquidity_label(r['days'], r['sold']), axis=1
    )

    # ── Агрегаты для листов "Товары" и "Кластеры" ──
    by_sku = df_plan.groupby(
        ['sku', 'sku_str', 'article', 'name', 'name_1c', 'barcode', 'price', 'stock_italco']
    ).agg(
        stock_total=('stock_total', 'sum'),
        in_transit=('in_transit', 'sum'),
        sold=('sold', 'sum'),
        daily=('daily', 'sum'),
        order=('order', 'sum'),
    ).reset_index()
    by_sku['days'] = (by_sku['stock_total'] / by_sku['daily'].replace(0, 0.0001)).round(0).clip(upper=999)
    by_sku.loc[by_sku['daily'] == 0, 'days'] = 999
    by_sku = by_sku.merge(last_sale_sku, on='sku', how='left')
    by_sku['days_no_sale'] = by_sku['last_sale_sku'].apply(
        lambda d: (today - d).days if pd.notna(d) else days_sales
    )
    by_sku['liquidity'] = by_sku.apply(
        lambda r: _liquidity_label(r['days'], r['sold']), axis=1
    )
    by_sku = by_sku.sort_values('order', ascending=False)

    # ── Сохранение Excel ──
    print(f"Сохранение {output}...")
    wb = Workbook()

    # ╔════════════════════════════════════════════════════════════════╗
    # ║ Листы в стиле Ozon (Наталья)                                  ║
    # ╚════════════════════════════════════════════════════════════════╝

    # Лист: Товар-склад (детализация SKU × конкретный склад)
    ws_ts = wb.active
    ws_ts.title = "Товар-склад"
    ws_ts.append([
        'Артикул', 'Название', 'SKU', 'Штрихкод', 'Кластер', 'Склад',
        'Свободно', 'В заказах', 'В пути',
    ])
    if not df_stock.empty:
        df_ts = df_stock.copy()
        df_ts['sku_str'] = df_ts['sku'].astype(str).str.replace('.0', '', regex=False)
        try:
            from db import get_catalog_sku_map
            ts_sku_to_art = get_catalog_sku_map(user_id=user_id)
        except Exception:
            ts_sku_to_art = {}
        df_ts['article'] = df_ts['sku_str'].map(ts_sku_to_art).fillna('')
        df_ts['barcode'] = df_ts['sku_str'].map(barcodes)
        df_ts['name_1c_v'] = df_ts['sku_str'].map(name_1c)
        df_ts = df_ts.sort_values(['cluster', 'item_name', 'warehouse_name'])
        for _, r in df_ts.iterrows():
            nm = r['name_1c_v'] if pd.notna(r['name_1c_v']) else r['item_name']
            ws_ts.append([
                str(r['article']) if r['article'] else '',
                str(nm),
                int(r['sku']) if pd.notna(r['sku']) else '',
                str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
                r['cluster'],
                r['warehouse_name'],
                int(r.get('free_to_sell_amount', 0) or 0),
                int(r.get('reserved_amount', 0) or 0),
                int(r.get('promised_amount', 0) or 0),
            ])

    # Лист: Товар-кластер (SKU × кластер с богатыми колонками)
    ws_tk = wb.create_sheet("Товар-кластер")
    ws_tk.append([
        'Артикул', 'Название', 'SKU', 'Штрихкод', 'Кластер',
        'Ликвидность', 'Дней до конца остатка',
        f'Среднесут. продажи за {days_sales}д', 'Дней без продаж',
        'Свободно', 'В пути', f'Продажи {days_sales}д', 'Заказать',
    ])
    df_tk = df_plan.sort_values(['cluster', 'order'], ascending=[True, False])
    for _, r in df_tk.iterrows():
        nm = r['name_1c'] if pd.notna(r['name_1c']) else r['name']
        ws_tk.append([
            str(r['article']) if r.get('article') else '',
            str(nm),
            int(r['sku']) if pd.notna(r['sku']) and r['sku'] else '',
            str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
            r['cluster'],
            r['liquidity'],
            int(r['days']),
            round(r['daily'], 2),
            int(r['days_no_sale']),
            int(r['stock_total']),
            int(r['in_transit']),
            int(r['sold']),
            int(r['order']),
        ])

    # Лист: Товары (агрегация по SKU)
    ws_tv = wb.create_sheet("Товары")
    ws_tv.append([
        'Артикул', 'Название', 'SKU', 'Штрихкод', 'Цена', 'Сток Италко',
        'Ликвидность', 'Дней до конца остатка',
        f'Среднесут. продажи за {days_sales}д', 'Дней без продаж',
        'Остаток всего', f'Продажи {days_sales}д', 'Заказать',
    ])
    for _, r in by_sku.iterrows():
        nm = r['name_1c'] if pd.notna(r['name_1c']) else r['name']
        ws_tv.append([
            str(r['article']) if r['article'] else '',
            str(nm),
            int(r['sku']) if pd.notna(r['sku']) and r['sku'] else '',
            str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
            r['price'] if pd.notna(r['price']) else '',
            str(r['stock_italco']) if pd.notna(r['stock_italco']) else '',
            r['liquidity'],
            int(r['days']),
            round(r['daily'], 2),
            int(r['days_no_sale']),
            int(r['stock_total']),
            int(r['sold']),
            int(r['order']),
        ])

    # Лист: Кластеры (агрегация по кластерам)
    ws_cl = wb.create_sheet("Кластеры")
    ws_cl.append([
        'Кластер', 'Ликвидность', 'Дней до конца остатка',
        f'Среднесут. продажи за {days_sales}д',
        'Остаток', f'Продажи {days_sales}д', 'Заказать',
    ])
    cluster_for_top = df_plan.groupby('cluster').agg(
        stock_total=('stock_total', 'sum'),
        sold=('sold', 'sum'),
        daily=('daily', 'sum'),
        order=('order', 'sum'),
    ).reset_index()
    cluster_for_top['days'] = (
        cluster_for_top['stock_total'] / cluster_for_top['daily'].replace(0, 0.001)
    ).round(0).clip(upper=999)
    cluster_for_top['liquidity'] = cluster_for_top.apply(
        lambda r: _liquidity_label(r['days'], r['sold']), axis=1
    )
    cluster_for_top = cluster_for_top.sort_values('days')
    for _, r in cluster_for_top.iterrows():
        ws_cl.append([
            r['cluster'], r['liquidity'], int(r['days']),
            round(r['daily'], 1), int(r['stock_total']),
            int(r['sold']), int(r['order']),
        ])

    # ╔════════════════════════════════════════════════════════════════╗
    # ║ Старые листы (наш привычный план)                              ║
    # ╚════════════════════════════════════════════════════════════════╝

    # Вкладка 1: Остатки по складам
    ws1 = wb.create_sheet("Остатки по складам")
    ws1.append(['Склад', 'Свободно', 'В заказах', 'В пути', 'Всего'])
    for _, r in stock_by_wh.iterrows():
        ws1.append([r['Склад'], int(r['Свободно']), int(r['В заказах']),
                     int(r['В пути']), int(r['Всего'])])
    ws1.append(['ИТОГО', int(stock_by_wh['Свободно'].sum()),
                int(stock_by_wh['В заказах'].sum()),
                int(stock_by_wh['В пути'].sum()),
                int(stock_by_wh['Всего'].sum())])

    # Вкладка 2: Приоритет кластеров
    cluster_sum = df_plan.groupby('cluster').agg({
        'stock_total': 'sum', 'sold': 'sum', 'daily': 'sum', 'order': 'sum',
    }).reset_index()
    cluster_sum['days'] = (
        cluster_sum['stock_total'] / cluster_sum['daily'].replace(0, 0.001)
    ).round(0).clip(upper=999)
    cluster_sum = cluster_sum.sort_values('days')

    ws2 = wb.create_sheet("Приоритет кластеров")
    ws2.append(['Кластер', 'Остаток', f'Продажи {days_sales}д',
                'Прод/день', 'Дней запаса', 'Заказать'])
    for _, r in cluster_sum.iterrows():
        ws2.append([r['cluster'], int(r['stock_total']), int(r['sold']),
                     round(r['daily'], 1), int(r['days']), int(r['order'])])

    # Вкладка 3: Магистраль кластеры
    if not df_mag.empty:
        ws3 = wb.create_sheet("Магистраль кластеры")
        ws3.append(['Кластер', 'Убытие/д', 'Спрос/д', '%', 'Статус'])
        for _, r in df_mag.iterrows():
            ws3.append([r['Кластер'], r['Убытие/д'], r['Спрос/д'],
                         f"{r['%']:+.0f}%", r['Статус']])

    # Вкладка 4: Магистраль товары
    if not magistral_detail.empty:
        ws4 = wb.create_sheet("Магистраль товары")
        ws4.append(['SKU', 'Название', 'Откуда (хомяк)', 'Куда (вампир)',
                     f'Кол-во {days_sales}д'])
        for _, r in magistral_detail.iterrows():
            ws4.append([int(r['SKU']) if r['SKU'] else '', str(r['Название']),
                         r['Откуда'], r['Куда'], int(r['Кол-во'])])

    # Вкладка 5: Сводный заказ
    df_cons = df_plan.groupby(
        ['sku', 'article', 'name', 'name_1c', 'barcode', 'price', 'stock_italco']
    ).agg({'stock_total': 'sum', 'sold': 'sum', 'order': 'sum'}).reset_index()
    df_cons = df_cons.sort_values('order', ascending=False)

    ws5 = wb.create_sheet("Сводный заказ")
    ws5.append(['Артикул', 'SKU', 'Название 1С', 'Штрихкод', 'Цена', 'Сток Италко',
                'Остаток Озон', f'Продажи {days_sales}д', 'Заказать'])
    for _, r in df_cons.iterrows():
        ws5.append([
            str(r['article']) if r['article'] else '',
            int(r['sku']) if r['sku'] else '',
            str(r['name_1c']) if pd.notna(r['name_1c']) else str(r['name']),
            str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
            r['price'] if pd.notna(r['price']) else '',
            str(r['stock_italco']) if pd.notna(r['stock_italco']) else '',
            int(r['stock_total']), int(r['sold']), int(r['order']),
        ])

    # Вкладка 6: Лента заказов
    df_lenta = df_plan[[
        'article', 'sku', 'name_1c', 'name', 'barcode', 'price', 'stock_italco',
        'cluster', 'stock', 'in_transit', 'stock_total', 'sold', 'days',
        'quant', 'order',
    ]].copy()
    df_lenta = df_lenta.sort_values(['cluster', 'order'], ascending=[True, False])

    ws6 = wb.create_sheet("Лента заказов")
    ws6.append(['Артикул', 'SKU', 'Название 1С', 'Штрихкод', 'Цена', 'Сток Италко',
                'Кластер', 'Остаток', 'В пути', 'Всего',
                f'Продажи {days_sales}д', 'Дней', 'Квант', 'Заказать'])
    for _, r in df_lenta.iterrows():
        ws6.append([
            str(r['article']) if r['article'] else '',
            int(r['sku']) if r['sku'] else '',
            str(r['name_1c']) if pd.notna(r['name_1c']) else str(r['name']),
            str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
            r['price'] if pd.notna(r['price']) else '',
            str(r['stock_italco']) if pd.notna(r['stock_italco']) else '',
            r['cluster'], int(r['stock']), int(r['in_transit']),
            int(r['stock_total']), int(r['sold']), int(r['days']),
            int(r['quant']), int(r['order']),
        ])

    # Вкладки по кластерам
    for cluster in cluster_sum['cluster'].tolist():
        cl = df_plan[
            (df_plan['cluster'] == cluster)
            & ((df_plan['stock_total'] + df_plan['sold']) > 0)
        ].sort_values('order', ascending=False)
        if cl.empty:
            continue
        ws = wb.create_sheet(cluster[:31])
        ws.append(['Артикул', 'SKU', 'Название 1С', 'Штрихкод', 'Цена', 'Сток Италко',
                    'Остаток', 'В пути', 'Всего', f'Прод {days_sales}д',
                    'Прод/д', 'Дней', 'Квант', 'Заказать'])
        for _, r in cl.iterrows():
            ws.append([
                str(r['article']) if r.get('article') else '',
                int(r['sku']) if r['sku'] else '',
                str(r['name_1c']) if pd.notna(r['name_1c']) else str(r['name']),
                str(int(r['barcode'])) if pd.notna(r['barcode']) else '',
                r['price'] if pd.notna(r['price']) else '',
                str(r['stock_italco']) if pd.notna(r['stock_italco']) else '',
                int(r['stock']), int(r['in_transit']), int(r['stock_total']),
                int(r['sold']), round(r['daily'], 2), int(r['days']),
                int(r['quant']), int(r['order']),
            ])

    # Форматирование
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    for ws in wb.worksheets:
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(
                max(len(str(c.value or '')) for c in col) + 2, 60)
        ws.freeze_panes = 'A2'

    # Сохраняем в BytesIO для скачивания через браузер
    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    return buf


# ── main ─────────────────────────────────────────────────────────────────

def main():
    build_supply_plan()


def load_cached_turnover():
    """Загрузить оборачиваемость из cache/."""
    from data_loader import load as _ld
    return _ld("stock_turnover")


def compute_supply_data(days_sales: int = DAYS_SALES, days_plan: int = DAYS_PLAN,
                        quantum_file: str = QUANTUM_FILE, creds=None, user_id=None):
    """Вычислить план поставок и магистраль без сохранения Excel.

    Returns dict with keys:
      cluster_priority: DataFrame (Кластер, Остаток, Продажи, Прод/день, Дней запаса, Заказать)
      magistral: DataFrame (Кластер, Убытие/д, Спрос/д, %, Статус)
      plan: DataFrame (full plan by sku x cluster)
    """
    df_q = load_quants(quantum_file)
    quants = dict(zip(df_q['SKU'], df_q['квант']))
    prices = dict(zip(df_q['SKU'], df_q['Цена']))

    raw_stocks = fetch_stocks(creds=creds)
    df_stock = process_stocks(raw_stocks)

    raw_postings = fetch_postings(days_sales, creds=creds)
    df_sales = process_sales(raw_postings)

    # Остатки по кластерам
    stock_cluster = df_stock.groupby(['sku', 'item_name', 'cluster']).agg({
        'free_to_sell_amount': 'sum', 'promised_amount': 'sum',
    }).reset_index()
    stock_cluster.columns = ['sku', 'name', 'cluster', 'stock', 'in_transit']
    stock_cluster['stock_total'] = stock_cluster['stock']  # только свободный остаток (без in_transit к покупателю)

    # Продажи по кластерам
    if not df_sales.empty:
        sales_cluster = df_sales.groupby(
            ['sku', 'name', 'dest_cluster']
        ).agg({'quantity': 'sum'}).reset_index()
        sales_cluster.columns = ['sku', 'name', 'cluster', 'sold']
        sales_cluster['daily'] = (sales_cluster['sold'] / days_sales).round(2)
    else:
        sales_cluster = pd.DataFrame(columns=['sku', 'name', 'cluster', 'sold', 'daily'])

    # План
    df_plan = stock_cluster.merge(
        sales_cluster[['sku', 'cluster', 'sold', 'daily']],
        on=['sku', 'cluster'], how='outer',
    ).fillna(0)
    df_plan['name'] = df_plan['name'].replace(0, '')
    if not sales_cluster.empty:
        nm = sales_cluster.set_index('sku')['name'].to_dict()
        df_plan['name'] = df_plan.apply(lambda r: r['name'] if r['name'] else nm.get(r['sku'], ''), axis=1)

    df_plan['sku_str'] = df_plan['sku'].astype(str).str.replace('.0', '', regex=False)
    df_plan['price'] = df_plan['sku_str'].map(prices)
    df_plan['quant'] = df_plan['sku_str'].map(quants).fillna(6).astype(int)

    # Map SKU → article from catalog DB
    try:
        from db import get_catalog_sku_map
        sku_to_art = get_catalog_sku_map(user_id=user_id)
        df_plan['article'] = df_plan['sku_str'].map(sku_to_art).fillna('')
    except Exception:
        df_plan['article'] = ''

    df_plan['days'] = (
        df_plan['stock_total'] / df_plan['daily'].replace(0, 0.0001)
    ).round(0).clip(upper=999)
    df_plan.loc[df_plan['daily'] == 0, 'days'] = 999
    df_plan['need'] = (
        (df_plan['daily'] * days_plan) - df_plan['stock'] - df_plan['in_transit']
    ).clip(lower=0).round(0)
    df_plan['boxes'] = np.ceil(df_plan['need'] / df_plan['quant']).astype(int)
    df_plan.loc[(df_plan['need'] > 0) & (df_plan['boxes'] == 0), 'boxes'] = 1
    df_plan.loc[
        (df_plan['stock_total'] == 0) & (df_plan['sold'] > 0) & (df_plan['boxes'] == 0),
        'boxes'
    ] = 1
    df_plan['order'] = df_plan['boxes'] * df_plan['quant']

    # Приоритет кластеров
    cluster_sum = df_plan.groupby('cluster').agg({
        'stock_total': 'sum', 'sold': 'sum', 'daily': 'sum', 'order': 'sum',
    }).reset_index()
    cluster_sum['days'] = (
        cluster_sum['stock_total'] / cluster_sum['daily'].replace(0, 0.001)
    ).round(0).clip(upper=999)
    cluster_sum = cluster_sum.sort_values('days')
    cluster_priority = cluster_sum.rename(columns={
        'cluster': 'Кластер', 'stock_total': 'Остаток', 'sold': 'Продажи',
        'daily': 'Прод/день', 'days': 'Дней запаса', 'order': 'Заказать',
    })

    # Магистраль
    if not df_sales.empty:
        outflow = df_sales.groupby('wh_cluster')['quantity'].sum().reset_index()
        outflow.columns = ['Кластер', 'Убытие']
        demand = df_sales.groupby('dest_cluster')['quantity'].sum().reset_index()
        demand.columns = ['Кластер', 'Спрос']
        df_mag = outflow.merge(demand, on='Кластер', how='outer').fillna(0)
        df_mag['Убытие/д'] = (df_mag['Убытие'] / days_sales).round(1)
        df_mag['Спрос/д'] = (df_mag['Спрос'] / days_sales).round(1)
        df_mag['%'] = (
            (df_mag['Убытие/д'] / df_mag['Спрос/д'].replace(0, 0.001) - 1) * 100
        ).round(0)
        df_mag['Статус'] = df_mag['%'].apply(
            lambda x: 'Хомяк' if x > 20 else ('Вампир' if x < -20 else 'Баланс'))
        df_mag = df_mag.sort_values('%', ascending=False)
    else:
        df_mag = pd.DataFrame()

    return {
        'cluster_priority': cluster_priority,
        'magistral': df_mag,
        'plan': df_plan,
        'sales': df_sales,
    }


if __name__ == "__main__":
    main()
