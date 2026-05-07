"""
Маржинальность Ozon — автономный скрипт.

Вход:
  - Прайс (себестоимость) из локального Excel
  - Ozon Seller API (транзакции)
  - Ozon Performance API (рекламные расходы / ДРР)

Выход:
  - ozon_margin_yesterday.xlsx
  - ozon_margin_month.xlsx
  - ozon_nachislen_yesterday.xlsx
  - ozon_nachislen_month.xlsx
"""

import pandas as pd
import requests
import time
import zipfile
import io
from datetime import datetime, timedelta, timezone

from utils.price_loader import load_price


def _get_creds(creds):
    """Fallback to config.py if creds not provided."""
    if creds is not None:
        return creds
    try:
        from config import (
            OZON_SELLER_CLIENT_ID, OZON_SELLER_API_KEY,
            OZON_PERF_CLIENT_ID, OZON_PERF_CLIENT_SECRET,
        )
        return {
            "OZON_SELLER_CLIENT_ID": OZON_SELLER_CLIENT_ID,
            "OZON_SELLER_API_KEY": OZON_SELLER_API_KEY,
            "OZON_PERF_CLIENT_ID": OZON_PERF_CLIENT_ID,
            "OZON_PERF_CLIENT_SECRET": OZON_PERF_CLIENT_SECRET,
        }
    except ImportError:
        return {}


def _seller_headers(creds):
    c = _get_creds(creds)
    return {
        "Client-Id": c["OZON_SELLER_CLIENT_ID"],
        "Api-Key": c["OZON_SELLER_API_KEY"],
        "Content-Type": "application/json",
    }


# ── Даты ─────────────────────────────────────────────────────────────────

def _iso_z(dt: datetime, end_of_day: bool = False) -> str:
    """Формирует дату для Ozon API.

    Передаём дату как есть (без конвертации MSK→UTC),
    чтобы совпадать с датами в ЛК Ozon.
    """
    if end_of_day:
        return dt.strftime("%Y-%m-%dT23:59:59.000Z")
    else:
        return dt.strftime("%Y-%m-%dT00:00:00.000Z")


# ── Seller API: транзакции ───────────────────────────────────────────────


def fetch_transactions(start_date: str, end_date: str, period_name: str,
                       creds=None) -> pd.DataFrame:
    url = "https://api-seller.ozon.ru/v3/finance/transaction/list"
    headers = _seller_headers(creds)
    payload = {
        "filter": {
            "date": {"from": start_date, "to": end_date},
            "operation_type": [],
            "posting_number": "",
            "transaction_type": "all",
        },
        "page": 1,
        "page_size": 1000,
    }

    all_transactions = []
    while True:
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            print(f"  Ошибка {resp.status_code} ({period_name})")
            break

        operations = resp.json().get("result", {}).get("operations", []) or []
        all_transactions.extend(operations)
        print(f"  Страница {payload['page']}: {len(operations)} записей")

        if len(operations) < payload["page_size"]:
            break
        payload["page"] += 1

    df = pd.DataFrame(all_transactions)
    print(f"  {period_name}: {len(df)} транзакций")

    if not df.empty and "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

    return df


# ── Развёртка items + amount ─────────────────────────────────────────────

def expand_items_with_amount(transactions_df: pd.DataFrame) -> pd.DataFrame:
    filtered = transactions_df[
        transactions_df.get("operation_type_name") == "Доставка покупателю"
    ]
    if filtered.empty:
        return pd.DataFrame(columns=["name", "sku", "amount"])

    rows = []
    for _, row in filtered.iterrows():
        items = row.get("items")
        if isinstance(items, list) and len(items) > 0:
            amount_per_item = row["amount"] / len(items)
            for item in items:
                rows.append({
                    "name": item.get("name", "Неизвестно"),
                    "sku": item.get("sku", 0),
                    "amount": amount_per_item,
                })
    return pd.DataFrame(rows)


def count_items(transactions_df: pd.DataFrame) -> pd.DataFrame:
    filtered = transactions_df[
        transactions_df.get("operation_type_name") == "Доставка покупателю"
    ]
    if filtered.empty or "items" not in filtered.columns:
        return pd.DataFrame(columns=["name", "sku", "count"])

    s = filtered["items"].explode().dropna()
    if s.empty:
        return pd.DataFrame(columns=["name", "sku", "count"])

    items_df = pd.json_normalize(s, max_level=1)
    if not {"name", "sku"}.issubset(items_df.columns):
        return pd.DataFrame(columns=["name", "sku", "count"])

    result = items_df[["name", "sku"]].value_counts().reset_index()
    result.columns = ["name", "sku", "count"]
    return result


# ── Начисления ───────────────────────────────────────────────────────────

def build_nachislen(transactions_df: pd.DataFrame, period_name: str) -> pd.DataFrame:
    if transactions_df.empty:
        return pd.DataFrame(columns=["operation_type_name", "amount"])

    if "operation_type_name" not in transactions_df.columns:
        transactions_df["operation_type_name"] = "Неизвестный тип"

    nachislen = (
        transactions_df
        .groupby("operation_type_name", as_index=False)["amount"]
        .sum()
    )
    total = pd.DataFrame([{
        "operation_type_name": "Общая сумма",
        "amount": nachislen["amount"].sum(),
    }])
    nachislen = pd.concat([nachislen, total], ignore_index=True)

    print(f"\nНачисления ({period_name}):")
    for _, r in nachislen.iterrows():
        print(f"  {r['operation_type_name']:55s} {r['amount']:>14,.2f}")
    return nachislen


# ── Performance API: ДРР ─────────────────────────────────────────────────

def _get_perf_token(creds=None) -> str | None:
    c = _get_creds(creds)
    resp = requests.post(
        "https://api-performance.ozon.ru/api/client/token",
        headers={"Content-Type": "application/json"},
        json={
            "client_id": c["OZON_PERF_CLIENT_ID"],
            "client_secret": c["OZON_PERF_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        },
    )
    if resp.status_code == 200:
        print("Performance API: токен получен")
        return resp.json()["access_token"]
    print(f"Performance API: ошибка {resp.status_code}")
    return None


def _get_campaigns(perf_headers: dict) -> list[dict]:
    resp = requests.get(
        "https://api-performance.ozon.ru/api/client/campaign",
        headers=perf_headers,
    )
    if resp.status_code != 200:
        print(f"  Ошибка получения кампаний: {resp.status_code}")
        return []

    campaigns = resp.json().get("list", [])
    # Только SKU-кампании: SEARCH_PROMO не поддерживается /api/client/statistics (400)
    filtered = [
        c for c in campaigns
        if c.get("advObjectType") == "SKU"
        and c.get("state") in ("CAMPAIGN_STATE_RUNNING", "CAMPAIGN_STATE_INACTIVE")
    ]
    print(f"  Кампаний: {len(campaigns)} -> {len(filtered)} (SKU)")
    return filtered


def _get_search_promo_spend(perf_headers: dict, date_from: str, date_to: str) -> float:
    """Расход SEARCH_PROMO кампаний через GET /api/client/statistics/daily."""
    resp = requests.get(
        "https://api-performance.ozon.ru/api/client/statistics/daily",
        headers=perf_headers,
        params={"dateFrom": date_from, "dateTo": date_to},
    )
    if resp.status_code != 200:
        print(f"  Ошибка daily stats: {resp.status_code}")
        return 0.0

    # CSV: ID;Название;Дата;Показы;Клики;Расход, ₽;Заказы, шт.;Заказы, ₽
    lines = resp.text.strip().split("\n")
    if len(lines) < 2:
        return 0.0

    # Собираем ID всех SKU-кампаний чтобы вычесть их из total
    # Проще: берём campaign details из /api/client/campaign и матчим по типу
    # Но в CSV нет типа — поэтому получаем список SKU campaign IDs
    resp2 = requests.get(
        "https://api-performance.ozon.ru/api/client/campaign",
        headers=perf_headers,
    )
    sku_ids = set()
    if resp2.status_code == 200:
        for c in resp2.json().get("list", []):
            if c.get("advObjectType") == "SKU":
                sku_ids.add(str(c["id"]))

    sp_spend = 0.0
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < 6:
            continue
        cid = parts[0].strip()
        if cid in sku_ids:
            continue
        try:
            sp_spend += float(parts[5].replace(",", "."))
        except ValueError:
            pass

    return sp_spend


def _request_report(perf_headers: dict, campaign_ids: list, date_from: str, date_to: str):
    for attempt in range(3):
        resp = requests.post(
            "https://api-performance.ozon.ru/api/client/statistics",
            headers=perf_headers,
            json={
                "campaigns": campaign_ids,
                "dateFrom": date_from,
                "dateTo": date_to,
                "groupBy": "DATE",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("UUID"), None
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"      Rate limit 429, жду {wait}с...")
            time.sleep(wait)
            continue
        return None, str(resp.status_code)
    return None, "429 (после 3 попыток)"


def _wait_for_report(perf_headers: dict, uuid: str, max_wait: int = 300) -> bool:
    url = f"https://api-performance.ozon.ru/api/client/statistics/{uuid}"
    for i in range(max_wait // 10):
        try:
            resp = requests.get(url, headers=perf_headers, timeout=15)
        except requests.exceptions.RequestException:
            time.sleep(10)
            continue
        if resp.status_code == 200:
            state = resp.json().get("state", "").upper()
            if state in ("OK", "DONE", "READY"):
                return True
            if i % 6 == 0:
                print(f"      {state} ({(i + 1) * 10}с)")
        time.sleep(10)
    return False


def _download_and_parse(perf_headers: dict, uuid: str):
    resp = requests.get(
        f"https://api-performance.ozon.ru/api/client/statistics/report?UUID={uuid}",
        headers=perf_headers,
    )
    if resp.status_code != 200:
        return None, 0

    all_data = []
    total_spent = 0

    if resp.content[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            with z.open(name) as f:
                lines = f.read().decode("utf-8-sig").strip().split("\n")
                if len(lines) < 2:
                    continue
                headers_list = lines[1].split(";")
                spent_idx = next(
                    (i for i, h in enumerate(headers_list) if "Расход" in h),
                    None,
                )
                for line in lines[2:]:
                    values = line.split(";")
                    if line.startswith("Всего") and spent_idx and len(values) > spent_idx:
                        try:
                            total_spent += float(values[spent_idx].replace(",", "."))
                        except ValueError:
                            pass
                        continue
                    if len(values) >= len(headers_list):
                        all_data.append(dict(zip(headers_list, values)))

    df = pd.DataFrame(all_data) if all_data else None
    return df, total_spent


def get_ads_by_sku(date_from: str, date_to: str, period_name: str,
                   creds=None) -> pd.DataFrame:
    print(f"\nДРР {period_name}: {date_from} — {date_to}")

    token = _get_perf_token(creds)
    if not token:
        return pd.DataFrame(columns=["sku", "ДРР"])

    perf_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    campaigns = _get_campaigns(perf_headers)

    all_dfs = []
    grand_total = 0

    if campaigns:
        campaign_ids = [c["id"] for c in campaigns]
        batch_size = 10
        total_batches = (len(campaign_ids) - 1) // batch_size + 1

        # Собираем пачки
        batches = [campaign_ids[i : i + batch_size]
                    for i in range(0, len(campaign_ids), batch_size)]

        failed_batches = []
        for batch_num, batch in enumerate(batches, 1):
            print(f"  Пачка {batch_num}/{total_batches}")

            uuid, err = _request_report(perf_headers, batch, date_from, date_to)
            if not uuid:
                print(f"    Ошибка: {err}")
                failed_batches.append(batch)
                time.sleep(10)
                continue

            if not _wait_for_report(perf_headers, uuid):
                print(f"    Таймаут ожидания")
                failed_batches.append(batch)
                continue

            df, batch_total = _download_and_parse(perf_headers, uuid)
            grand_total += batch_total

            if df is not None and not df.empty:
                all_dfs.append(df)
                print(f"    SKU: {len(df)}, расход: {batch_total:,.0f}")
            else:
                print(f"    расход: {batch_total:,.0f}")

            time.sleep(5)

        # Ретрай неудавшихся пачек
        if failed_batches:
            print(f"  Повторная попытка: {len(failed_batches)} пачек")
            time.sleep(30)
            for batch in failed_batches:
                uuid, err = _request_report(perf_headers, batch, date_from, date_to)
                if not uuid:
                    print(f"    Повтор не удался: {err}")
                    continue
                if not _wait_for_report(perf_headers, uuid):
                    print(f"    Повтор: таймаут")
                    continue
                df, batch_total = _download_and_parse(perf_headers, uuid)
                grand_total += batch_total
                if df is not None and not df.empty:
                    all_dfs.append(df)
                    print(f"    Повтор OK: {len(df)} SKU, расход: {batch_total:,.0f}")
                time.sleep(5)

    # Расход SEARCH_PROMO (не даёт per-SKU, добавляем как «Продвижение в поиске»)
    sp_spend = _get_search_promo_spend(perf_headers, date_from, date_to)
    if sp_spend > 0:
        grand_total += sp_spend
        print(f"  Продвижение в поиске: {sp_spend:,.0f}")

    print(f"  Итого ДРР: {grand_total:,.0f}")

    if not all_dfs:
        return pd.DataFrame(columns=["sku", "ДРР"])

    full_df = pd.concat(all_dfs, ignore_index=True)
    spent_col = next((c for c in full_df.columns if "Расход" in c), None)

    if "sku" not in full_df.columns or not spent_col:
        return pd.DataFrame(columns=["sku", "ДРР"])

    full_df["sku"] = pd.to_numeric(full_df["sku"], errors="coerce")
    full_df[spent_col] = full_df[spent_col].str.replace(",", ".").astype(float)

    result = full_df.groupby("sku", as_index=False)[spent_col].sum()
    result.columns = ["sku", "ДРР"]
    result = result[result["sku"] > 0]
    result["sku"] = result["sku"].astype(int)

    # SEARCH_PROMO: атрибуции по SKU нет, не размазываем — иначе крупные SKU
    # получают чужие расходы пропорционально своему собственному ДРР, что искажает
    # юнит-экономику. Расход SEARCH_PROMO виден отдельно в начислениях
    # ("Продвижение в поиске") и в общей сводке маркетплейса.

    print(f"  SKU с ДРР: {len(result)}")
    return result


# ── Сборка итоговой таблицы ──────────────────────────────────────────────

def build_final(
    transactions_df: pd.DataFrame,
    price: pd.DataFrame,
    ads_df: pd.DataFrame,
    period_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает (final_result, nachislen)."""

    # Развёртка
    expanded = expand_items_with_amount(transactions_df)
    items = count_items(transactions_df)

    if expanded.empty or items.empty:
        print(f"  {period_name}: нет данных по доставкам")
        empty = pd.DataFrame()
        return empty, build_nachislen(transactions_df, period_name)

    aggregated = expanded.groupby("name", as_index=False).agg({"amount": "sum"})

    # Себестоимость
    if "Ozon SKU ID" in price.columns:
        final = items.merge(
            price[["Ozon SKU ID", "Цена в рублях"]],
            left_on="sku",
            right_on="Ozon SKU ID",
            how="left",
        ).rename(columns={"Цена в рублях": "Себестоимость"})
        final = final[["name", "sku", "count", "Себестоимость"]]
        final["Сумма себестоимости"] = final["count"] * final["Себестоимость"]
    else:
        print("  Колонка 'Ozon SKU ID' не найдена в прайсе!")
        final = items.copy()
        final["Себестоимость"] = 0
        final["Сумма себестоимости"] = 0

    # Выручка
    final = final.merge(aggregated[["name", "amount"]], on="name", how="left")

    # ДРР из Performance API
    final = final.merge(ads_df, on="sku", how="left")
    final["ДРР"] = final["ДРР"].fillna(0).astype(int)

    # Расчёты
    final.rename(columns={"amount": "Сумма отгрузки"}, inplace=True)

    final["Прибыль"] = final["Сумма отгрузки"] - final["Сумма себестоимости"]
    final["Маржинальность (%)"] = (
        final["Прибыль"] / final["Сумма отгрузки"] * 100
    ).round(2)
    final["Доля продаж (%)"] = (
        final["Сумма отгрузки"] / final["Сумма отгрузки"].sum() * 100
    ).round(2)
    final["ДРР (%)"] = (
        final["ДРР"] / final["Сумма отгрузки"] * 100
    ).fillna(0).round(2)
    final["Маржа с учетом ДРР (%)"] = (
        (final["Сумма отгрузки"] - final["Сумма себестоимости"] - final["ДРР"])
        / final["Сумма отгрузки"]
        * 100
    ).fillna(0).round(2)

    final = final.sort_values("Сумма отгрузки", ascending=False).reset_index(drop=True)

    # Начисления
    nachislen = build_nachislen(transactions_df, period_name)

    # Итоги
    total_otgruzka = nachislen.loc[
        nachislen["operation_type_name"] == "Общая сумма", "amount"
    ].values[0]
    total_sebes = final["Сумма себестоимости"].sum()
    sebes_ratio = (total_sebes / total_otgruzka) * 100 if total_otgruzka else 0

    print(f"\nИтоги {period_name}:")
    print(f"  SKU: {len(final)}")
    print(f"  Сумма начислений:    {total_otgruzka:>14,.2f}")
    print(f"  Себестоимость:       {total_sebes:>14,.2f} ({sebes_ratio:.1f}%)")
    print(f"  Маржа:               {100 - sebes_ratio:.1f}%")
    print(f"  ДРР:                 {final['ДРР'].sum():>14,.0f}")

    return final, nachislen


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("МАРЖИНАЛЬНОСТЬ OZON")
    print("=" * 60)

    # Даты
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    month_start = yesterday.replace(day=1)

    date_from_y = _iso_z(yesterday)
    date_to_y = _iso_z(yesterday, end_of_day=True)
    date_from_m = _iso_z(month_start)
    date_to_m = _iso_z(yesterday, end_of_day=True)

    date_perf_y = yesterday.strftime("%Y-%m-%d")
    date_perf_m = month_start.strftime("%Y-%m-%d")

    print(f"Вчера: {yesterday.strftime('%d.%m.%Y')}")
    print(f"Месяц: с {month_start.strftime('%d.%m.%Y')}")

    # Прайс
    from config import PRICE_FILE
    price = load_price(PRICE_FILE)

    # Транзакции
    print("\nЗагрузка транзакций за вчера...")
    tx_yesterday = fetch_transactions(date_from_y, date_to_y, "Вчера")

    print("\nЗагрузка транзакций за месяц...")
    tx_month = fetch_transactions(date_from_m, date_to_m, "Месяц")

    # ДРР
    ads_yesterday = get_ads_by_sku(date_perf_y, date_perf_y, "за вчера")
    ads_month = get_ads_by_sku(date_perf_m, date_perf_y, "за месяц")

    # Финальные таблицы
    final_y, nach_y = build_final(tx_yesterday, price, ads_yesterday, "за вчера")
    final_m, nach_m = build_final(tx_month, price, ads_month, "за месяц")

    # Сохранение
    if not final_y.empty:
        final_y.to_excel("ozon_margin_yesterday.xlsx", index=False)
        print("\nСохранено: ozon_margin_yesterday.xlsx")
    if not final_m.empty:
        final_m.to_excel("ozon_margin_month.xlsx", index=False)
        print("Сохранено: ozon_margin_month.xlsx")

    nach_y.to_excel("ozon_nachislen_yesterday.xlsx", index=False)
    nach_m.to_excel("ozon_nachislen_month.xlsx", index=False)
    print("Сохранено: ozon_nachislen_yesterday.xlsx, ozon_nachislen_month.xlsx")

    print("\nГотово.")


def load_cached(period="month"):
    """Загрузить результаты из cache/. period: 'yesterday' или 'month'."""
    from data_loader import load as _ld
    s = "y" if period == "yesterday" else "m"
    return _ld(f"oz_final_{s}"), _ld(f"oz_nach_{s}")


if __name__ == "__main__":
    main()
