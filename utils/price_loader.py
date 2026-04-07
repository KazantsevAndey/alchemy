import pandas as pd


def load_price(file_path: str) -> pd.DataFrame:
    """Загружает прайс-лист с себестоимостью из Excel-файла.

    Возвращает DataFrame с колонками: Артикул, Ozon SKU ID, Наименование, Цена в рублях.
    Артикул приводится к строке (strip).
    """
    price = pd.read_excel(file_path)
    if "Артикул" in price.columns:
        price["Артикул"] = price["Артикул"].astype(str).str.strip()
    print(f"Прайс загружен: {len(price)} товаров из {file_path}")
    return price


def build_cost_map_from_db(user_id: int) -> dict:
    """Build cost map from DB catalog. Returns {article: cost_price}."""
    from db import get_cost_map
    return get_cost_map(user_id)


def build_cost_map(price: pd.DataFrame, key_col: str = "Артикул") -> dict:
    """Строит словарь {sku: себестоимость} из прайса."""
    cost_map = {}
    for _, row in price.iterrows():
        sku = str(row.get(key_col, "")).strip()
        cost = pd.to_numeric(row.get("Цена в рублях"), errors="coerce")
        if sku and pd.notna(cost) and cost > 0:
            cost_map[sku] = cost
    return cost_map
