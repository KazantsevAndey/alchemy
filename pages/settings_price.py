"""Settings page: Product catalog & cost prices."""

import streamlit as st
import pandas as pd
import io

from db import (
    get_catalog, get_catalog_count, upsert_catalog_items,
    update_cost_prices, get_cost_map,
)
from user_context import get_user_credentials, has_marketplace_credentials


def _export_excel(catalog: list[dict]) -> bytes:
    """Export catalog to Excel for offline editing."""
    df = pd.DataFrame(catalog)
    cols = ["article", "ozon_sku", "name", "cost_price"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].rename(columns={
        "article": "Артикул",
        "ozon_sku": "Ozon SKU ID",
        "name": "Наименование",
        "cost_price": "Цена в рублях",
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Каталог")
    return buf.getvalue()


def render(user_id: int):
    st.title("Себестоимость")

    tab1, tab2, tab3 = st.tabs(["Каталог товаров", "Загрузить файл", "Кванты поставок"])

    with tab1:
        _render_catalog(user_id)

    with tab2:
        _render_upload(user_id)

    with tab3:
        _render_quants(user_id)


def _render_catalog(user_id: int):
    """Main tab: fetch catalog from APIs, edit cost prices inline."""

    creds = get_user_credentials(user_id)
    has_any_mp = (
        has_marketplace_credentials(user_id, "ozon") or
        has_marketplace_credentials(user_id, "wb") or
        has_marketplace_credentials(user_id, "ym")
    )

    catalog_count = get_catalog_count(user_id)

    # ── Status ──────────────────────────────────────────────────────
    if catalog_count > 0:
        cost_map = get_cost_map(user_id)
        filled = len(cost_map)
        st.info(f"В каталоге **{catalog_count}** товаров, себестоимость указана у **{filled}**")
    else:
        st.warning("Каталог пуст. Подключите API-ключи и нажмите «Сформировать каталог».")

    # ── Fetch button ────────────────────────────────────────────────
    if not has_any_mp:
        st.caption("Добавьте API-ключи на странице «API-ключи», чтобы загрузить каталог.")
    else:
        if st.button("Сформировать каталог", type="primary"):
            _fetch_catalog(user_id, creds)
            st.rerun()

    if catalog_count == 0:
        return

    # ── Editable table ──────────────────────────────────────────────
    st.subheader("Редактирование цен")

    catalog = get_catalog(user_id)
    df = pd.DataFrame(catalog)

    # Prepare display DataFrame
    display_df = pd.DataFrame({
        "Артикул": df["article"],
        "Наименование": df["name"],
        "Себестоимость": df["cost_price"].astype(float),
        "Ozon": df["ozon_sku"].apply(lambda x: True if x else False),
        "WB": df["wb_nm_id"].apply(lambda x: True if x else False),
        "YM": df["ym_market_sku"].apply(lambda x: True if x else False),
    })

    edited = st.data_editor(
        display_df,
        column_config={
            "Артикул": st.column_config.TextColumn(disabled=True),
            "Наименование": st.column_config.TextColumn(disabled=True),
            "Себестоимость": st.column_config.NumberColumn(
                min_value=0, max_value=999999, step=0.01, format="%.2f"
            ),
            "Ozon": st.column_config.CheckboxColumn(disabled=True),
            "WB": st.column_config.CheckboxColumn(disabled=True),
            "YM": st.column_config.CheckboxColumn(disabled=True),
        },
        use_container_width=True,
        num_rows="fixed",
        key="price_editor",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Сохранить цены", type="primary"):
            updates = {}
            for i, row in edited.iterrows():
                art = row["Артикул"]
                cost = float(row["Себестоимость"]) if pd.notna(row["Себестоимость"]) else 0.0
                orig = float(display_df.loc[i, "Себестоимость"])
                if cost != orig:
                    updates[art] = cost
            if updates:
                update_cost_prices(user_id, updates)
                st.success(f"Обновлено {len(updates)} цен")
                st.rerun()
            else:
                st.info("Нет изменений")

    with col2:
        st.download_button(
            "Скачать Excel",
            data=_export_excel(catalog),
            file_name="catalog_prices.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def _fetch_catalog(user_id: int, creds: dict):
    """Fetch product catalogs from all connected marketplaces."""
    import time as _t
    from catalog_fetcher import fetch_ozon_catalog, fetch_wb_catalog, fetch_ym_catalog, merge_catalogs

    ozon_items, wb_items, ym_items = [], [], []

    with st.status("Загрузка каталога...", expanded=True) as status:
        if creds.get("OZON_SELLER_CLIENT_ID"):
            st.write("Ozon: загрузка...")
            t0 = _t.time()
            ozon_items = fetch_ozon_catalog(creds)
            st.write(f"Ozon: {len(ozon_items)} товаров ({_t.time()-t0:.1f}с)")

        if creds.get("WB_API_KEY"):
            st.write("WB: загрузка...")
            t0 = _t.time()
            wb_items = fetch_wb_catalog(creds)
            st.write(f"WB: {len(wb_items)} товаров ({_t.time()-t0:.1f}с)")

        if creds.get("YM_API_KEY"):
            # Pass known articles from Ozon/WB so YM doesn't scan 20k+ items
            known = {it["article"] for it in ozon_items + wb_items}
            st.write(f"YM: загрузка ({len(known)} артикулов)...")
            t0 = _t.time()
            ym_items = fetch_ym_catalog(creds, known_articles=known if known else None)
            st.write(f"YM: {len(ym_items)} товаров ({_t.time()-t0:.1f}с)")

        merged = merge_catalogs(ozon_items, wb_items, ym_items)
        st.write(f"Итого уникальных артикулов: {len(merged)}")

        if merged:
            t0 = _t.time()
            upsert_catalog_items(user_id, merged)
            st.write(f"Сохранено за {_t.time()-t0:.1f}с")
            status.update(label=f"Каталог обновлён: {len(merged)} товаров", state="complete")
        else:
            status.update(label="Товары не найдены", state="error")


def _render_upload(user_id: int):
    """Upload tab: import cost prices from Excel."""

    st.markdown("""
Загрузите Excel-файл с себестоимостью. Обязательные колонки:
- **Артикул** — артикул товара
- **Себестоимость** (или «Цена в рублях») — цена за 1 шт.
""")

    uploaded = st.file_uploader("Выберите файл", type=["xlsx", "xls"])
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

    # Find article column
    art_col = None
    for col in df.columns:
        if str(col).lower().strip() in ["артикул", "article", "offer_id", "vendorcode", "sku",
                                         "артикул поставщика", "артикул продавца"]:
            art_col = col
            break
    if art_col is None and "Артикул" in df.columns:
        art_col = "Артикул"

    # Find price column
    price_col = None
    for col in df.columns:
        if str(col).lower().strip() in ["себестоимость", "цена в рублях", "цена", "cost",
                                         "price", "cost_price", "закупочная цена", "закупка"]:
            price_col = col
            break

    if art_col is None or price_col is None:
        st.error(f"Не найдены колонки «Артикул» и/или «Себестоимость»")
        st.caption(f"Колонки в файле: {', '.join(df.columns.tolist())}")
        return

    df[art_col] = df[art_col].astype(str).str.strip()
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")

    valid = df[df[art_col].notna() & (df[art_col] != "") & (df[art_col] != "nan")
               & df[price_col].notna() & (df[price_col] > 0)].copy()
    valid = valid.drop_duplicates(subset=art_col, keep="last")

    if valid.empty:
        st.error("Нет строк с корректным артикулом и ценой")
        return

    st.success(f"Найдено **{len(valid)}** товаров с ценами")

    # Preview
    st.dataframe(valid[[art_col, price_col]].head(15), use_container_width=True)

    if st.button("Загрузить цены", type="primary"):
        # Upsert articles that might not be in catalog yet
        items = [{"article": row[art_col], "name": ""} for _, row in valid.iterrows()]
        upsert_catalog_items(user_id, items)

        # Update cost prices
        updates = {str(row[art_col]).strip(): float(row[price_col])
                   for _, row in valid.iterrows()}
        update_cost_prices(user_id, updates)

        st.success(f"Загружено {len(updates)} цен")
        st.rerun()


def _get_quantum_path(user_id: int) -> str:
    """Path to user's quantum file."""
    from pathlib import Path
    d = Path(f"data/user_{user_id}")
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "quantum_stock.xlsx")


def _export_quantum_template(catalog: list[dict]) -> bytes:
    """Generate quantum template Excel from catalog."""
    df = pd.DataFrame(catalog)
    cols_out = pd.DataFrame({
        "Артикул": df["article"].astype(str).str.strip(),
        "SKU": df["ozon_sku"].fillna("") if "ozon_sku" in df.columns else "",
        "Название": df["name"].fillna("") if "name" in df.columns else "",
        "квант": 1,
        "Цена": df["cost_price"].fillna(0) if "cost_price" in df.columns else 0,
    })
    buf = io.BytesIO()
    cols_out.to_excel(buf, index=False, sheet_name="Кванты")
    return buf.getvalue()


def _render_quants(user_id: int):
    """Tab for managing quantum (packaging units) file."""
    from pathlib import Path

    quantum_path = _get_quantum_path(user_id)
    has_file = Path(quantum_path).exists()

    if has_file:
        try:
            existing = pd.read_excel(quantum_path)
            st.success(f"Файл квантов загружен: **{len(existing)}** строк")
            st.dataframe(existing.head(20), use_container_width=True)
        except Exception as e:
            st.error(f"Ошибка чтения файла: {e}")
    else:
        st.info("Файл квантов не загружен. Поставки рассчитываются с квантом = 1 по умолчанию.")

    st.divider()

    # Download template
    catalog_count = get_catalog_count(user_id)
    if catalog_count > 0:
        catalog = get_catalog(user_id)
        st.download_button(
            "Скачать шаблон квантов",
            data=_export_quantum_template(catalog),
            file_name="quantum_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption("Шаблон содержит артикулы из каталога. Заполните колонку «квант» и загрузите обратно.")
    else:
        st.caption("Сначала сформируйте каталог товаров, чтобы скачать шаблон.")

    # Upload
    uploaded = st.file_uploader("Загрузить файл квантов", type=["xlsx", "xls"], key="quantum_upload")
    if uploaded:
        try:
            df = pd.read_excel(uploaded)
        except Exception as e:
            st.error(f"Не удалось прочитать файл: {e}")
            return

        if df.empty:
            st.error("Файл пустой")
            return

        # Validate required columns
        has_article = "Артикул" in df.columns
        has_quant = "квант" in df.columns
        if not has_article or not has_quant:
            st.error(f"Нужны колонки «Артикул» и «квант». В файле: {', '.join(df.columns.tolist())}")
            return

        st.success(f"Найдено **{len(df)}** строк")
        st.dataframe(df.head(10), use_container_width=True)

        if st.button("Сохранить кванты", type="primary", key="save_quants"):
            df.to_excel(quantum_path, index=False)
            st.success("Файл квантов сохранён")
            st.rerun()
