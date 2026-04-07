"""Settings page: Price list upload with strict validation."""

import streamlit as st
import pandas as pd
import io
from pathlib import Path
from datetime import datetime

from db import get_active_price, save_price_list
from user_context import get_user_data_dir


# ── Required columns ─────────────────────────────────────────────────────

REQUIRED_COLS = ["Артикул", "Цена в рублях"]

# Optional but recognized columns (will be kept if present)
OPTIONAL_COLS = ["Наименование", "Ozon SKU ID"]

# Fuzzy column name mapping: user might name them differently
_COL_ALIASES = {
    "Артикул": ["артикул", "article", "sku", "артикул поставщика", "offer_id",
                 "артикул продавца", "vendor code", "vendorcode", "код товара"],
    "Цена в рублях": ["цена в рублях", "себестоимость", "цена", "cost", "price",
                        "себес", "закупочная цена", "закупка", "cost_price",
                        "цена закупки"],
    "Наименование": ["наименование", "название", "name", "товар", "product",
                      "название товара"],
    "Ozon SKU ID": ["ozon sku id", "ozon sku", "sku id", "ozon_sku_id",
                      "sku ozon"],
}


def _match_columns(df: pd.DataFrame) -> tuple[dict, list]:
    """Try to map DataFrame columns to expected names.

    Returns:
        (rename_map, missing_required)
    """
    rename_map = {}
    found = set()

    for target, aliases in _COL_ALIASES.items():
        # Exact match first
        if target in df.columns:
            found.add(target)
            continue

        # Fuzzy match
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if col_lower in aliases:
                rename_map[col] = target
                found.add(target)
                break

    missing = [c for c in REQUIRED_COLS if c not in found]
    return rename_map, missing


def _generate_template() -> bytes:
    """Generate downloadable Excel template."""
    df = pd.DataFrame({
        "Артикул": ["8000604001306", "4640165782296", "8003012015071"],
        "Наименование": ["Кофе Illy зерно 250г", "Чай Ahmad Earl Grey", "Lavazza Qualita Oro"],
        "Цена в рублях": [450.0, 320.0, 890.0],
        "Ozon SKU ID": [123456789, 987654321, 555666777],
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Себестоимость")
    return buf.getvalue()


def render(user_id: int):
    st.title("Прайс-лист")

    # Current price info
    active = get_active_price(user_id)
    if active:
        st.info(
            f"Текущий прайс: **{active['filename']}** "
            f"({active['sku_count']} SKU, загружен {active['uploaded_at'][:16]})"
        )
    else:
        st.warning("Прайс-лист не загружен. Себестоимость не будет учитываться.")

    st.divider()

    # Template download
    st.subheader("Формат файла")
    st.markdown("""
**Обязательные колонки:**
- **Артикул** — артикул товара (совпадает с артикулом на маркетплейсах)
- **Цена в рублях** — себестоимость за 1 шт.

**Необязательные колонки:**
- **Наименование** — название товара
- **Ozon SKU ID** — числовой SKU из Ozon (для точного матча)
""")

    st.download_button(
        "Скачать шаблон",
        data=_generate_template(),
        file_name="price_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()

    # Upload
    uploaded = st.file_uploader(
        "Загрузите Excel с себестоимостью",
        type=["xlsx", "xls"],
    )

    if not uploaded:
        return

    try:
        df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Не удалось прочитать файл: {e}")
        return

    if df.empty:
        st.error("Файл пустой")
        return

    # ── Column matching ──────────────────────────────────────────────
    rename_map, missing = _match_columns(df)

    if rename_map:
        st.info("Автоматически распознаны колонки: " +
                ", ".join(f"«{k}» → **{v}**" for k, v in rename_map.items()))
        df = df.rename(columns=rename_map)

    if missing:
        st.error(f"Не найдены обязательные колонки: **{', '.join(missing)}**")
        st.caption(f"Колонки в файле: {', '.join(df.columns.tolist())}")
        st.markdown("Скачайте шаблон выше и заполните по образцу.")
        return

    # ── Data validation ──────────────────────────────────────────────
    errors = []
    warnings = []

    # Clean article
    df["Артикул"] = df["Артикул"].astype(str).str.strip()
    df = df[df["Артикул"].notna() & (df["Артикул"] != "") & (df["Артикул"] != "nan")]

    if len(df) == 0:
        st.error("Нет строк с заполненным артикулом")
        return

    # Clean price
    df["Цена в рублях"] = pd.to_numeric(df["Цена в рублях"], errors="coerce")

    no_price = df["Цена в рублях"].isna().sum()
    if no_price > 0:
        warnings.append(f"{no_price} строк без цены (будут пропущены)")

    negative = (df["Цена в рублях"] < 0).sum()
    if negative > 0:
        errors.append(f"{negative} строк с отрицательной ценой")

    zero_price = (df["Цена в рублях"] == 0).sum()
    if zero_price > 0:
        warnings.append(f"{zero_price} строк с нулевой ценой")

    # Duplicates
    dupes = df[df["Артикул"].duplicated(keep=False)]
    if len(dupes) > 0:
        n_dupes = df["Артикул"].duplicated().sum()
        warnings.append(f"{n_dupes} дубликатов артикулов (будет взята последняя строка)")

    # Suspicious prices
    valid_prices = df["Цена в рублях"].dropna()
    if len(valid_prices) > 0:
        if valid_prices.max() > 100_000:
            warnings.append(f"Есть цены > 100 000 ₽ (макс: {valid_prices.max():,.0f})")
        if valid_prices.min() < 1 and valid_prices.min() > 0:
            warnings.append(f"Есть цены < 1 ₽ (мин: {valid_prices.min():.2f})")

    if errors:
        for e in errors:
            st.error(e)
        st.markdown("Исправьте ошибки и загрузите файл заново.")
        return

    for w in warnings:
        st.warning(w)

    # ── Valid rows ────────────────────────────────────────────────────
    valid = df[df["Цена в рублях"].notna() & (df["Цена в рублях"] > 0)].copy()

    # Deduplicate (keep last)
    valid = valid.drop_duplicates(subset="Артикул", keep="last")
    sku_count = len(valid)

    if sku_count == 0:
        st.error("Нет строк с корректной ценой")
        return

    st.success(f"**{sku_count}** SKU с ценой (из {len(df)} строк в файле)")

    # ── Preview ──────────────────────────────────────────────────────
    st.subheader("Превью")
    preview_cols = [c for c in ["Артикул", "Наименование", "Цена в рублях", "Ozon SKU ID"]
                    if c in valid.columns]
    st.dataframe(
        valid[preview_cols].head(15).style.format({"Цена в рублях": "{:,.2f}"}),
        use_container_width=True,
    )

    # Price stats
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("SKU", sku_count)
    with c2:
        st.metric("Средняя цена", f"{valid['Цена в рублях'].mean():,.0f} ₽")
    with c3:
        st.metric("Медиана", f"{valid['Цена в рублях'].median():,.0f} ₽")

    # ── Apply ────────────────────────────────────────────────────────
    if st.button("Применить прайс", type="primary"):
        data_dir = get_user_data_dir(user_id)

        # Save current
        current_path = data_dir / "price_current.xlsx"
        uploaded.seek(0)
        current_path.write_bytes(uploaded.read())

        # Save backup
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        uploaded.seek(0)
        (data_dir / f"price_{ts}.xlsx").write_bytes(uploaded.read())

        # Record in DB
        save_price_list(
            user_id=user_id,
            filename=uploaded.name,
            file_path=str(current_path),
            sku_count=sku_count,
        )

        st.success(f"Прайс применён: {uploaded.name} ({sku_count} SKU)")
        st.rerun()
