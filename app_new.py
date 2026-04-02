"""
Alchemy dashboard — multi-page, cache-driven.
streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from data_loader import load as _dl_load, cache_age_minutes, cache_timestamp
from config import PRICE_FILE

st.set_page_config(page_title="Alchemy", layout="wide",
                   initial_sidebar_state="expanded")

# ── Lovable-ref design system ─────────────────────────────────────────────
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
    --bg: #f0f2f5;
    --card: #ffffff;
    --foreground: #1e293b;
    --muted: #64748b;
    --border: #e2e8f0;
    --ozon: #005BFF;
    --wb: #8B5CF6;
    --success: #059669;
    --destructive: #dc2626;
    --warning: #ca8a04;
    --radius: 12px;
    --shadow-card: 0 1px 3px 0 rgba(0,0,0,0.02), 0 4px 12px -2px rgba(0,0,0,0.05);
    --shadow-hover: 0 10px 15px -3px rgba(0,0,0,0.05);
    --sidebar-bg: #15203b;
    --sidebar-fg: #e2e8f0;
    --sidebar-muted: #64748b;
    --sidebar-active: #005BFF;
}
/* Global font */
html, body, .stApp, .stMarkdown, .stMetric, [data-testid="stMetricValue"],
[data-testid="stRadio"], [data-testid="stSelectbox"], .stTextInput input,
[data-testid="stNumberInput"] input, [data-testid="stButton"] button {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}
/* Background */
.stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--bg) !important;
}
/* Metric values: tabular nums */
[data-testid="stMetricValue"], .metric-value {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-feature-settings: 'tnum' on, 'lnum' on !important;
    letter-spacing: -0.04em !important;
}
/* Card-matte style */
.card-matte {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-card);
    transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
}
.card-matte:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-hover);
}
/* Section labels */
.section-label {
    font-size: 11px; font-weight: 700; letter-spacing: 1.5px;
    text-transform: uppercase; color: var(--muted);
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 12px; margin-top: 28px;
}
.section-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
/* Sidebar dark theme */
[data-testid="stSidebar"] {
    background-color: var(--sidebar-bg) !important;
    border-right: none !important;
}
[data-testid="stSidebar"] * {
    color: var(--sidebar-fg) !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p,
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2 {
    color: var(--sidebar-fg) !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    color: var(--sidebar-muted) !important;
    transition: color 0.15s;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    color: var(--sidebar-fg) !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label[data-checked="true"],
[data-testid="stSidebar"] [aria-checked="true"] + label {
    color: var(--sidebar-fg) !important;
    background: rgba(0, 91, 255, 0.1) !important;
    border-radius: 8px;
}
[data-testid="stSidebar"] .stButton button {
    background-color: var(--sidebar-active) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.1) !important;
}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {
    color: var(--sidebar-muted) !important;
}
/* Hide Streamlit chrome */
h1 a, h2 a, h3 a { display: none !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }
/* Streamlit containers inherit card style */
[data-testid="stExpander"] {
    border-radius: var(--radius) !important;
    border-color: var(--border) !important;
}
/* Clean dataframe styling */
[data-testid="stDataFrame"] {
    border-radius: var(--radius) !important;
}
/* Metric cards in main area */
[data-testid="stMetric"] {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 20px;
    box-shadow: var(--shadow-card);
}
/* Fade-in-up animation */
@keyframes fadeInUp {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}
.fade-in-up {
    animation: fadeInUp 0.4s cubic-bezier(0.2, 0, 0, 1) forwards;
}
</style>
""", unsafe_allow_html=True)

# ── dates ─────────────────────────────────────────────────────────────────

now = datetime.now()
yesterday = now - timedelta(days=1)
month_start = yesterday.replace(day=1)
YSTR = yesterday.strftime("%d.%m.%Y")
MSTR = f'{month_start.strftime("%d.%m")} — {yesterday.strftime("%d.%m.%Y")}'

# ── cache loader ─────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def C(key):
    return _dl_load(key)

# ── helpers ───────────────────────────────────────────────────────────────

def _fm(v):
    """Format number with spaces as thousands separator."""
    sign = "−" if v < 0 else ""
    return f'{sign}{abs(v):,.0f}'.replace(",", " ")

def _delta_str(v):
    """Format delta for st.metric."""
    if v >= 0:
        return f'{_fm(v)} ₽'
    return f'{_fm(v)} ₽'

def _oz(final, nach):
    if nach is None or final is None:
        return {"rev": 0, "sebes": 0, "drr": 0, "profit": 0, "margin": 0, "sku": 0}
    tr = nach.loc[nach["operation_type_name"] == "Общая сумма", "amount"]
    rev = tr.values[0] if len(tr) else 0
    sebes = final["Сумма себестоимости"].sum() if not final.empty else 0
    drr = final["ДРР"].sum() if not final.empty else 0
    profit = rev - sebes
    margin = (profit / rev * 100) if rev else 0
    return {"rev": rev, "sebes": sebes, "drr": drr, "profit": profit, "margin": margin, "sku": len(final)}

def _wb(summary, agg):
    if summary is None or agg is None:
        return {"rev": 0, "sebes": 0, "drr": 0, "profit": 0, "margin": 0, "vyr": 0, "sku": 0}
    rev = summary["itogo"]
    sebes = agg["sebes_total"].sum() if not agg.empty else 0
    drr = agg["adv_sum"].sum() if not agg.empty else 0
    vyr = agg["vyruchka"].sum() if not agg.empty else 0
    profit = rev - sebes - drr
    margin = (profit / vyr * 100) if vyr else 0
    return {"rev": rev, "sebes": sebes, "drr": drr, "profit": profit, "margin": margin, "vyr": vyr, "sku": len(agg)}

def _mc(val):
    """Margin color for dataframe styling: green >30%, teal 20-30%, yellow 15-20%, red <15%."""
    try: v = float(val)
    except: return ""
    if v >= 30: return "background-color:rgba(5,150,105,0.15);color:#059669"
    if v >= 20: return "background-color:rgba(6,182,212,0.15);color:#0891b2"
    if v >= 15: return "background-color:rgba(202,138,4,0.15);color:#ca8a04"
    return "background-color:rgba(220,38,38,0.15);color:#dc2626"

def _margin_bar(val):
    """Colored background + thin progress-bar at bottom of cell."""
    try: v = float(val)
    except: return ""
    w = max(0, min(100, v / 50 * 100))
    if v >= 30:
        bg = "rgba(5,150,105,0.15)"; tc = "#059669"; bc = "#34d399"
    elif v >= 20:
        bg = "rgba(6,182,212,0.15)"; tc = "#0891b2"; bc = "#06b6d4"
    elif v >= 15:
        bg = "rgba(202,138,4,0.15)"; tc = "#ca8a04"; bc = "#fbbf24"
    else:
        bg = "rgba(220,38,38,0.15)"; tc = "#dc2626"; bc = "#f87171"
    return (
        f"background: linear-gradient(90deg, {bc} {w:.0f}%, #e5e7eb {w:.0f}%) "
        f"no-repeat bottom left, {bg}; background-size: 100% 6px, 100% 100%; "
        f"padding-bottom: 10px; color: {tc}; font-weight: 500"
    )

def _sc(val):
    """Stock days color for dataframe styling."""
    try: v = int(val)
    except: return ""
    if v <= 14: return "background-color:rgba(220,38,38,0.1);color:#dc2626"
    if v <= 30: return "background-color:rgba(202,138,4,0.1);color:#ca8a04"
    if v <= 60: return "background-color:rgba(22,163,74,0.1);color:#16a34a"
    return ""

CARD_TITLES = {
    "Ozon": ("OZON", "#005BFF", "#005BFF"),
    "WB":   ("WILDBERRIES", "#8B5CF6", "#8B5CF6"),
    "Итого": ("ИТОГО", "#1e293b", "#1e293b"),
}
CARD_NAVS = {"Ozon": "🔵 Ozon", "WB": "🟣 WB"}

def _card(title, rev, profit, margin, btn_key=None):
    """Lovable-ref style card with colored pill on left."""
    name, name_color, pill_color = CARD_TITLES.get(title, (title, "#1e293b", "#1e293b"))
    m_color = "#059669" if margin >= 20 else "#ca8a04" if margin >= 15 else "#dc2626"
    p_color = "#059669" if profit >= 0 else "#dc2626"
    clickable = title in CARD_NAVS

    card_inner = (
        f'<div style="display:flex;gap:16px;padding:24px;align-items:stretch">'
        # Colored pill
        f'<div style="width:4px;border-radius:4px;background:{pill_color};flex-shrink:0;align-self:stretch"></div>'
        f'<div style="flex:1;min-width:0">'
        # Brand label
        f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
        f'color:{name_color};margin-bottom:8px">{name}</div>'
        # Large margin
        f'<div style="font-size:32px;font-weight:700;color:{m_color};line-height:1.2;'
        f'font-feature-settings:\'tnum\' on,\'lnum\' on;letter-spacing:-0.04em">{margin:.1f}%</div>'
        f'<div style="font-size:11px;color:#64748b;margin-bottom:12px">маржа</div>'
        # Revenue + Profit row
        f'<div style="display:flex;gap:24px">'
        f'<div>'
        f'<div style="font-size:11px;color:#64748b">Выручка</div>'
        f'<div style="font-size:14px;font-weight:600;color:#1e293b;'
        f'font-feature-settings:\'tnum\' on,\'lnum\' on;letter-spacing:-0.04em">{_fm(rev)} ₽</div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:11px;color:#64748b">Прибыль</div>'
        f'<div style="font-size:14px;font-weight:600;color:{p_color};'
        f'font-feature-settings:\'tnum\' on,\'lnum\' on;letter-spacing:-0.04em">{_fm(profit)} ₽</div>'
        f'</div>'
        f'</div>'
        f'</div></div>'
    )

    card_cls = "card-matte" if not clickable else "card-matte"
    if clickable:
        st.markdown(
            f'<style>'
            f'.alch-card-link,.alch-card-link:hover,.alch-card-link:visited,.alch-card-link:active'
            f'{{text-decoration:none!important;display:block;color:inherit}}'
            f'</style>'
            f'<a href="?nav={title}" target="_self" class="alch-card-link">'
            f'<div class="card-matte" style="cursor:pointer">{card_inner}</div></a>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="card-matte">{card_inner}</div>',
            unsafe_allow_html=True,
        )

def _summary_row(label, oz, wb, suffix=""):
    """Render one row of 3 cards: Ozon | WB | Итого."""
    t_rev = oz["rev"] + wb["rev"]
    t_prf = oz["profit"] + wb["profit"]
    t_mar = (t_prf / t_rev * 100) if t_rev else 0

    section_name = label.upper()
    st.markdown(f'<div class="section-label">{section_name}</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _card("Ozon", oz["rev"], oz["profit"], oz["margin"], f"oz_{suffix}")
    with c2:
        _card("WB", wb["rev"], wb["profit"], wb["margin"], f"wb_{suffix}")
    with c3:
        _card("Итого", t_rev, t_prf, t_mar)


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════

_NAV_MAP = {"Ozon": "🔵 Ozon", "WB": "🟣 WB"}

# Handle ?nav= query parameter from clickable cards/donuts
qp = st.query_params
if "nav" in qp:
    target = _NAV_MAP.get(qp["nav"])
    if target:
        st.session_state["nav"] = target
    if "exp" in qp:
        st.session_state["open_exp"] = qp["exp"]  # "y" or "m"
        del qp["exp"]
    del qp["nav"]

with st.sidebar:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;padding:8px 0 16px 0">'
        '<span style="font-size:24px">⚗️</span>'
        '<span style="font-size:20px;font-weight:700;letter-spacing:-0.02em">Alchemy</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    pages = [
        "⚗️ Сводка",
        "🔵 Ozon",
        "🟣 WB",
        "🚛 Поставки",
    ]
    nav_idx = 0
    if "nav" in st.session_state and st.session_state["nav"] in pages:
        nav_idx = pages.index(st.session_state["nav"])
        del st.session_state["nav"]
    page = st.radio("Навигация", pages, index=nav_idx, label_visibility="collapsed")

    st.divider()
    age = cache_age_minutes()
    ts = cache_timestamp()
    if age is not None:
        st.caption(f"Обновлено: {ts}")
    else:
        st.caption("⚠️ Кэш отсутствует")

    btn_label = "Загрузить данные" if age is None else "Обновить данные"
    if st.button(btn_label, use_container_width=True,
                 type="primary" if age is None or age >= 60 else "secondary"):
        from data_loader import refresh_all_data
        with st.spinner("Загрузка данных с API..."):
            refresh_all_data()
        st.cache_data.clear()
        st.rerun()


# ── gate ─────────────────────────────────────────────────────────────────

if cache_age_minutes() is None:
    st.info("Нажмите «Загрузить данные» в боковой панели")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# ⚗️ Сводка
# ══════════════════════════════════════════════════════════════════════════

if page == "⚗️ Сводка":
    st.title("Сводка")
    st.caption(MSTR)

    oz_y = _oz(C("oz_final_y"), C("oz_nach_y"))
    wb_y = _wb(C("wb_sum_y"), C("wb_agg_y"))
    oz_m = _oz(C("oz_final_m"), C("oz_nach_m"))
    wb_m = _wb(C("wb_sum_m"), C("wb_agg_m"))

    _summary_row("Вчера", oz_y, wb_y, "y")
    _summary_row("Месяц", oz_m, wb_m, "m")

    # ── Donut charts: структура расходов ──
    st.divider()

    # Short name mapping for long Ozon operation types
    _OZ_SHORT = {
        "Оплата эквайринга": "Эквайринг",
        "Обработка товара в составе грузоместа на FBO": "Обработка ГМ",
        "Доставка и обработка возврата, отмены, невыкупа": "Возвраты",
        "Получение возврата, отмены, невыкупа от покупателя": "Приём возвратов",
        "Услуга размещения товаров на складе": "Хранение",
        "Продвижение с оплатой за заказ": "Продвижение",
        "Подписка Premium Plus": "Premium Plus",
        "Обработка сроков годности на FBO": "Сроки годности",
        "Декомпенсации и возвращение товаров на сток": "Декомпенсации",
        "Временное размещение товара в СЦ/ПВЗ": "Врем. размещение",
        "Временное размещение товара партнерами": "Врем. разм. партн.",
        "Вывоз товара со Склада силами Ozon: Доставка до ПВЗ": "Вывоз до ПВЗ",
        "Вывоз товара со Склада силами Ozon: Доставка до СЦ": "Вывоз до СЦ",
        "Обеспечение материалами для упаковки товара": "Упак. материалы",
        "Упаковка товара партнёрами": "Упаковка",
        "Утилизация товара: Автоутилизация возвратов и отмен": "Утил. возвратов",
        "Утилизация товара: Автоутилизация со стока": "Утил. со стока",
        "Утилизация товара: Вы заказали утилизацию": "Утил. заказ",
        "Утилизация товара: Вы не забрали в срок": "Утил. просрочка",
        "Утилизация товара: Повреждённые из-за упаковки": "Утил. повреждения",
        "Утилизация товара: Пролились/просыпались из-за упаковки": "Утил. пролив",
        "Услуга по бронированию места и персонала для поставки с неполным составом в составе ГМ": "Бронь поставки",
        "Услуга по обработке опознанных излишков в составе ГМ": "Излишки ГМ",
    }
    DONUT_COLORS = [
        "#005BFF", "#f59e0b", "#ef4444", "#059669", "#8B5CF6",
        "#ec4899", "#06b6d4", "#f97316", "#84cc16", "#64748b",
    ]
    def _topN_donut(labels, values, title, key, top_n=8, nav_link=None):
        """Build top-N + Прочие donut chart in a styled container.
        nav_link: e.g. '?nav=Ozon&exp=y' to make the donut clickable.
        """
        import pandas as pd
        df = pd.DataFrame({"label": labels, "value": values})
        df = df.sort_values("value", ascending=False).reset_index(drop=True)
        total = df["value"].sum()
        if len(df) > top_n:
            top = df.iloc[:top_n].copy()
            other = df.iloc[top_n:]["value"].sum()
            top = pd.concat([top, pd.DataFrame([{"label": "Прочие", "value": other}])], ignore_index=True)
            df = top
        fig = go.Figure(go.Pie(
            labels=df["label"].tolist(),
            values=df["value"].tolist(),
            hole=0.55,
            marker=dict(colors=DONUT_COLORS[:len(df)]),
            textinfo="label+value",
            textposition="outside",
            texttemplate="%{label}<br>%{value:,.0f}",
            hovertemplate="%{label}<br>%{value:,.0f} ₽ (%{percent})<extra></extra>",
            sort=False,
        ))
        fig.update_layout(
            height=280,
            showlegend=True,
            legend=dict(orientation="v", x=1.02, y=0.5, font=dict(size=10)),
            margin=dict(l=10, r=10, t=10, b=30),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(
                text=f'<b>{_fm(total)}</b>',
                x=0.5, y=0.5, font_size=15, showarrow=False,
                font_color="#1e293b",
            )],
        )
        with st.container(border=True):
            if nav_link:
                st.markdown(
                    f'<style>'
                    f'.donut-link{{text-decoration:none!important;color:inherit!important;display:block}}'
                    f'.donut-link:hover{{text-decoration:none!important;color:inherit!important}}'
                    f'</style>'
                    f'<a href="{nav_link}" target="_self" class="donut-link">'
                    f'<b>{title}</b> →</a>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{title}**")
            st.plotly_chart(fig, use_container_width=True, key=key,
                                config={"displayModeBar": False})

    def _oz_donut(nach_key, title, chart_key, nav_link=None):
        oz_nach = C(nach_key)
        if oz_nach is not None and not oz_nach.empty:
            neg = oz_nach[
                (oz_nach["operation_type_name"] != "Общая сумма") &
                (oz_nach["operation_type_name"] != "Доставка покупателю") &
                (oz_nach["operation_type_name"] != "Доставка покупателю — отмена начисления") &
                (oz_nach["amount"] < 0)
            ].copy()
            neg["abs_amount"] = neg["amount"].abs()
            neg = neg.sort_values("abs_amount", ascending=False).reset_index(drop=True)
            short_labels = [_OZ_SHORT.get(n, n) for n in neg["operation_type_name"]]
            _topN_donut(short_labels, neg["abs_amount"].tolist(), title, chart_key, top_n=8, nav_link=nav_link)

    def _wb_donut(df_key, sum_key, agg_key, title, chart_key, nav_link=None):
        wb_df = C(df_key)
        if wb_df is not None and not wb_df.empty:
            wb_expenses = {}
            for col, label in [
                ("delivery_rub", "Логистика"),
                ("storage_fee", "Хранение"),
                ("deduction", "Удержания"),
                ("penalty", "Штрафы"),
                ("rebill_logistic_cost", "Обр. логистика"),
            ]:
                if col in wb_df.columns:
                    val = wb_df[col].sum()
                    if val > 0:
                        wb_expenses[label] = val
            wb_sum_data = C(sum_key)
            if wb_sum_data:
                if wb_sum_data.get("loyal_cost", 0) > 0:
                    wb_expenses["Лояльность"] = wb_sum_data["loyal_cost"]
                if wb_sum_data.get("loyal_balls", 0) > 0:
                    wb_expenses["Баллы лояльн."] = wb_sum_data["loyal_balls"]
            wb_agg = C(agg_key)
            if wb_agg is not None and not wb_agg.empty and "drr" in wb_agg.columns:
                drr_val = wb_agg["drr"].sum()
                if drr_val > 0:
                    wb_expenses["ДРР (реклама)"] = drr_val
            if wb_expenses:
                _topN_donut(list(wb_expenses.keys()), list(wb_expenses.values()), title, chart_key, top_n=8, nav_link=nav_link)

    # --- Вчера ---
    st.markdown("**Структура расходов (вчера)**")
    yc1, yc2 = st.columns(2)
    with yc1:
        _oz_donut("oz_nach_y", "Ozon — расходы", "oz_donut_y", nav_link="?nav=Ozon&exp=y")
    with yc2:
        _wb_donut("wb_df_y", "wb_sum_y", "wb_agg_y", "WB — расходы", "wb_donut_y", nav_link="?nav=WB&exp=y")

    # --- Месяц ---
    st.markdown("**Структура расходов (месяц)**")
    mc1, mc2 = st.columns(2)
    with mc1:
        _oz_donut("oz_nach_m", "Ozon — расходы", "oz_donut_m", nav_link="?nav=Ozon&exp=m")
    with mc2:
        _wb_donut("wb_df_m", "wb_sum_m", "wb_agg_m", "WB — расходы", "wb_donut_m", nav_link="?nav=WB&exp=m")

    # ── Топ-15 SKU по выручке ──
    st.divider()
    oz_final_top = C("oz_final_m")
    if oz_final_top is not None and not oz_final_top.empty:
        top15 = (oz_final_top
                 .sort_values("Сумма отгрузки", ascending=False)
                 .head(15)
                 .sort_values("Сумма отгрузки", ascending=True))

        drr_pct = (top15["ДРР"] / top15["Сумма отгрузки"] * 100).fillna(0)
        fig = go.Figure(go.Bar(
            y=top15["name"], x=top15["Сумма отгрузки"],
            orientation="h",
            marker_color="#005BFF",
            customdata=list(zip(
                top15["Маржинальность (%)"], top15["ДРР"],
                top15["Прибыль"], drr_pct,
            )),
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Выручка: %{x:,.0f} ₽<br>"
                "Маржинальность: %{customdata[0]:.1f}%<br>"
                "ДРР: %{customdata[1]:,.0f} ₽ (%{customdata[3]:.1f}%)<br>"
                "Прибыль: %{customdata[2]:,.0f} ₽"
                "<extra></extra>"
            ),
        ))
        fig.update_layout(
            height=520,
            margin=dict(l=10, r=10, t=10, b=10),
            bargap=0.3,
            showlegend=False,
            xaxis_title="Выручка, ₽",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        with st.container(border=True):
            st.markdown("**Ozon — Топ-15 SKU по выручке (месяц)**")
            st.plotly_chart(fig, use_container_width=True, key="oz_top15",
                            config={"displayModeBar": False})

    # ── WB Топ-15 SKU ──
    wb_agg_top = C("wb_agg_m")
    if wb_agg_top is not None and not wb_agg_top.empty:
        wt = wb_agg_top.copy()
        wt["label"] = wt["Наименование"].fillna(wt["sa_name"])
        top15w = (wt.sort_values("vyruchka", ascending=False)
                  .head(15)
                  .sort_values("vyruchka", ascending=True))

        drr_pct_w = (top15w["adv_sum"] / top15w["vyruchka"] * 100).fillna(0)
        fig_w = go.Figure(go.Bar(
            y=top15w["label"], x=top15w["vyruchka"],
            orientation="h",
            marker_color="#8B5CF6",
            customdata=list(zip(
                top15w["margin"], top15w["adv_sum"],
                top15w["profit_with_drr"], drr_pct_w,
            )),
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Выручка: %{x:,.0f} ₽<br>"
                "Маржинальность: %{customdata[0]:.1f}%<br>"
                "ДРР: %{customdata[1]:,.0f} ₽ (%{customdata[3]:.1f}%)<br>"
                "Прибыль: %{customdata[2]:,.0f} ₽"
                "<extra></extra>"
            ),
        ))
        fig_w.update_layout(
            height=520,
            margin=dict(l=10, r=10, t=10, b=10),
            bargap=0.3,
            showlegend=False,
            xaxis_title="Выручка, ₽",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        with st.container(border=True):
            st.markdown("**WB — Топ-15 SKU по выручке (месяц)**")
            st.plotly_chart(fig_w, use_container_width=True, key="wb_top15",
                            config={"displayModeBar": False})


# ══════════════════════════════════════════════════════════════════════════
# 🔵 Ozon
# ══════════════════════════════════════════════════════════════════════════

elif page == "🔵 Ozon":
    # Load data for both periods
    oz_final_y = C("oz_final_y"); oz_nach_y = C("oz_nach_y")
    oz_final_m = C("oz_final_m"); oz_nach_m = C("oz_nach_m")
    if oz_final_m is None:
        st.warning("Нет данных. Обновите кэш.")
        st.stop()

    oy = _oz(oz_final_y, oz_nach_y)
    om = _oz(oz_final_m, oz_nach_m)

    # ── 3 cards: marketplace | yesterday | month (lovable-ref style) ──
    def _info_card_oz(html, pill_color="#005BFF"):
        st.markdown(
            f'<div class="card-matte" style="height:100%">'
            f'<div style="display:flex;gap:16px;padding:24px;align-items:stretch">'
            f'<div style="width:4px;border-radius:4px;background:{pill_color};flex-shrink:0;align-self:stretch"></div>'
            f'<div style="flex:1">{html}</div>'
            f'</div></div>',
            unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _info_card_oz(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#005BFF;margin-bottom:8px">OZON</div>'
            f'<div style="font-size:12px;color:#64748b">SKU с продажами (месяц)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#1e293b;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{om["sku"]}</div>'
            f'<div style="font-size:11px;color:#64748b;margin-top:8px">{MSTR}</div>'
        )
    with c2:
        ym_color = "#059669" if oy["margin"] >= 20 else "#ca8a04" if oy["margin"] >= 15 else "#dc2626"
        yp_color = "#059669" if oy["profit"] >= 0 else "#dc2626"
        _info_card_oz(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#005BFF;margin-bottom:8px">ВЧЕРА</div>'
            f'<div style="font-size:32px;font-weight:700;color:{ym_color};line-height:1.2;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{oy["margin"]:.1f}%</div>'
            f'<div style="font-size:11px;color:#64748b;margin-bottom:12px">маржа</div>'
            f'<div style="display:flex;gap:24px">'
            f'<div><div style="font-size:11px;color:#64748b">Отгрузка</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:#1e293b">{_fm(oy["rev"])} ₽</div></div>'
            f'<div><div style="font-size:11px;color:#64748b">Прибыль</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:{yp_color}">{_fm(oy["profit"])} ₽</div></div>'
            f'</div>'
        )
    with c3:
        mm_color = "#059669" if om["margin"] >= 20 else "#ca8a04" if om["margin"] >= 15 else "#dc2626"
        mp_color = "#059669" if om["profit"] >= 0 else "#dc2626"
        _info_card_oz(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#005BFF;margin-bottom:8px">МЕСЯЦ</div>'
            f'<div style="font-size:32px;font-weight:700;color:{mm_color};line-height:1.2;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{om["margin"]:.1f}%</div>'
            f'<div style="font-size:11px;color:#64748b;margin-bottom:12px">маржа</div>'
            f'<div style="display:flex;gap:24px">'
            f'<div><div style="font-size:11px;color:#64748b">Отгрузка</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:#1e293b">{_fm(om["rev"])} ₽</div></div>'
            f'<div><div style="font-size:11px;color:#64748b">Прибыль</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:{mp_color}">{_fm(om["profit"])} ₽</div></div>'
            f'</div>'
        )

    # ── Period radio ──
    oz_period = st.radio("Период", ["Месяц", "Вчера"], horizontal=True, key="oz_period")
    _oz_nach_key = "oz_nach_m" if oz_period == "Месяц" else "oz_nach_y"
    _oz_final_key = "oz_final_m" if oz_period == "Месяц" else "oz_final_y"
    oz_nach = C(_oz_nach_key)
    oz_final = C(_oz_final_key)
    o_cur = _oz(oz_final, oz_nach)

    # ── Donut: expense structure ──
    if oz_nach is not None and not oz_nach.empty:
        neg = oz_nach[
            (oz_nach["operation_type_name"] != "Общая сумма") &
            (oz_nach["operation_type_name"] != "Доставка покупателю") &
            (oz_nach["operation_type_name"] != "Доставка покупателю — отмена начисления") &
            (oz_nach["amount"] < 0)
        ].copy()
        if not neg.empty:
            neg["abs_amount"] = neg["amount"].abs()
            exp_agg = neg.groupby("operation_type_name")["abs_amount"].sum().reset_index()
            exp_agg = exp_agg.sort_values("abs_amount", ascending=False)

            # Top-5 + "Прочие" for donut
            top5 = exp_agg.head(5).copy()
            rest = exp_agg.iloc[5:]
            if not rest.empty:
                other = pd.DataFrame([{"operation_type_name": "Прочие", "abs_amount": rest["abs_amount"].sum()}])
                donut_df = pd.concat([top5, other], ignore_index=True)
            else:
                donut_df = top5
            donut_df["label"] = donut_df.apply(
                lambda r: f'{r["operation_type_name"]}<br>{_fm(r["abs_amount"])} ₽', axis=1)
            fig = px.pie(donut_df, values="abs_amount", names="operation_type_name",
                         hole=0.55, color_discrete_sequence=px.colors.qualitative.Set2)
            fig.update_traces(textposition="outside", textinfo="label+value",
                              text=donut_df["label"], texttemplate="%{text}",
                              pull=[0.02] * len(donut_df))
            fig.update_layout(height=420, margin=dict(t=30, b=10, l=80, r=80),
                              title="Структура расходов", showlegend=False,
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True, key="oz_donut",
                            config={"displayModeBar": False})

            total = neg["abs_amount"].sum()
            tbl_exp = pd.DataFrame({
                "Тип расхода": exp_agg["operation_type_name"].tolist() + ["Итого"],
                "Сумма ₽": exp_agg["abs_amount"].tolist() + [total],
            })
            st.dataframe(
                tbl_exp.style.format({"Сумма ₽": "{:,.0f}"})
                .apply(lambda x: ["font-weight:bold" if x["Тип расхода"] == "Итого" else "" for _ in x], axis=1),
                use_container_width=True, hide_index=True,
            )

    # ── Юнит-экономика по SKU ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#005BFF"></span>ЮНИТ-ЭКОНОМИКА ПО SKU</div>', unsafe_allow_html=True)

    oz_unit_data = oz_final
    if oz_unit_data is not None and not oz_unit_data.empty:
        fc = st.columns([3, 1, 1, 1, 1])
        with fc[0]:
            q = st.text_input("Поиск", key="oz_q", placeholder="Название...",
                              label_visibility="collapsed")
        with fc[1]:
            mr_from = st.number_input("Маржа от %", value=-50, step=1, key="oz_mr_from")
        with fc[2]:
            mr_to = st.number_input("Маржа до %", value=80, step=1, key="oz_mr_to")
        with fc[3]:
            mq = st.number_input("Мин шт", 0, step=1, key="oz_mq")
        with fc[4]:
            df_ = st.selectbox("ДРР", ["Все", "С ДРР", "Без ДРР"], key="oz_df")

        tbl = oz_unit_data[["name", "sku", "count", "Сумма отгрузки",
                         "Сумма себестоимости", "ДРР", "Прибыль",
                         "Маржинальность (%)", "Маржа с учетом ДРР (%)",
                         "Доля продаж (%)"]].copy()
        tbl.columns = ["Название", "SKU", "Шт", "Выручка", "Себестоимость",
                        "ДРР", "Прибыль", "Маржа %", "Маржа с ДРР %", "Доля %"]
        if q:
            tbl = tbl[tbl["Название"].str.contains(q, case=False, na=False)]
        tbl = tbl[(tbl["Маржа с ДРР %"] >= mr_from) & (tbl["Маржа с ДРР %"] <= mr_to)]
        if mq > 0:
            tbl = tbl[tbl["Шт"] >= mq]
        if df_ == "С ДРР":
            tbl = tbl[tbl["ДРР"] > 0]
        elif df_ == "Без ДРР":
            tbl = tbl[tbl["ДРР"] == 0]
        tbl = tbl.sort_values("Выручка", ascending=False).reset_index(drop=True)

        fmt = {"Выручка": "{:,.0f}", "Себестоимость": "{:,.0f}",
               "ДРР": "{:,.0f}", "Прибыль": "{:,.0f}",
               "Маржа %": "{:.1f}%", "Маржа с ДРР %": "{:.1f}%",
               "Доля %": "{:.1f}%", "Шт": "{:.0f}"}
        styled = (tbl.style.format(fmt)
                  .map(_margin_bar, subset=["Маржа %", "Маржа с ДРР %"]))
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} из {len(oz_unit_data)} SKU")

    # ── Анализ ДРР ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#005BFF"></span>АНАЛИЗ ДРР</div>', unsafe_allow_html=True)

    oz_drr_period = st.radio("Период", ["Месяц", "Вчера"], horizontal=True, key="oz_drr_period")
    oz_drr_src = C("oz_final_m") if oz_drr_period == "Месяц" else C("oz_final_y")

    if oz_drr_src is not None and not oz_drr_src.empty and "ДРР" in oz_drr_src.columns:
        drr_data = oz_drr_src[oz_drr_src["ДРР"] > 0].copy()
        total_drr = oz_drr_src["ДРР"].sum()
        total_rev = oz_drr_src["Сумма отгрузки"].sum()
        drr_pct = (total_drr / total_rev * 100) if total_rev else 0

        dc1, dc2, dc3 = st.columns(3)
        dc1.metric("Общий ДРР", f"{_fm(total_drr)} ₽")
        dc2.metric("ДРР % от выручки", f"{drr_pct:.1f}%")
        dc3.metric("SKU с рекламой", str(len(drr_data)))

        if not drr_data.empty:
            drr_tbl = drr_data[["name", "sku", "ДРР", "Сумма отгрузки",
                                "Маржинальность (%)", "Маржа с учетом ДРР (%)"]].copy()
            drr_tbl["ДРР %"] = (drr_tbl["ДРР"] / drr_tbl["Сумма отгрузки"].replace(0, 0.001) * 100).round(1)
            drr_tbl = drr_tbl.rename(columns={
                "name": "Название", "sku": "SKU", "ДРР": "Расход ₽",
                "Сумма отгрузки": "Выручка ₽",
                "Маржинальность (%)": "Маржа без ДРР %",
                "Маржа с учетом ДРР (%)": "Маржа с ДРР %",
            })
            drr_tbl = drr_tbl.sort_values("Расход ₽", ascending=False).reset_index(drop=True)

            def _drr_color(val):
                try: v = float(val)
                except: return ""
                if v > 10: return "background-color:rgba(220,38,38,0.15);color:#dc2626"
                if v >= 5: return "background-color:rgba(202,138,4,0.15);color:#ca8a04"
                return ""

            drr_fmt = {"Расход ₽": "{:,.0f}", "Выручка ₽": "{:,.0f}",
                       "ДРР %": "{:.1f}%", "Маржа без ДРР %": "{:.1f}%",
                       "Маржа с ДРР %": "{:.1f}%"}
            st.dataframe(
                drr_tbl.style.format(drr_fmt)
                .map(_drr_color, subset=["ДРР %"])
                .map(_margin_bar, subset=["Маржа без ДРР %", "Маржа с ДРР %"]),
                use_container_width=True, hide_index=True, height=500,
            )

            # ── Bar chart: top-15 by revenue, DRR overlay ──
            chart_df = oz_drr_src[["name", "Сумма отгрузки", "ДРР", "Маржа с учетом ДРР (%)"]].copy()
            chart_df = chart_df.sort_values("Сумма отгрузки", ascending=False).head(15)
            chart_df = chart_df.sort_values("Сумма отгрузки", ascending=True)
            chart_df["ДРР %"] = (chart_df["ДРР"] / chart_df["Сумма отгрузки"].replace(0, 0.001) * 100).round(1)
            chart_df["short"] = chart_df["name"].str[:40]

            fig_drr = go.Figure()
            fig_drr.add_trace(go.Bar(
                y=chart_df["short"], x=chart_df["Сумма отгрузки"],
                orientation="h", name="Выручка",
                marker_color="#005BFF",
                customdata=chart_df[["name", "Сумма отгрузки", "ДРР", "ДРР %", "Маржа с учетом ДРР (%)"]].values,
                hovertemplate="<b>%{customdata[0]}</b><br>Выручка: %{customdata[1]:,.0f} ₽<br>"
                              "ДРР: %{customdata[2]:,.0f} ₽<br>ДРР: %{customdata[3]:.1f}%<br>"
                              "Маржа с ДРР: %{customdata[4]:.1f}%<extra></extra>",
            ))
            fig_drr.add_trace(go.Bar(
                y=chart_df["short"], x=chart_df["ДРР"],
                orientation="h", name="ДРР",
                marker_color="#dc2626",
                hoverinfo="skip",
            ))
            fig_drr.update_layout(
                barmode="overlay", height=max(350, len(chart_df) * 28),
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                yaxis=dict(showgrid=False),
            )
            st.plotly_chart(fig_drr, use_container_width=True, key="oz_drr_chart",
                            config={"displayModeBar": False})
    else:
        st.info("Нет данных по ДРР за выбранный период")


# ══════════════════════════════════════════════════════════════════════════
# 🟣 WB
# ══════════════════════════════════════════════════════════════════════════

elif page == "🟣 WB":
    # Load data for both periods
    wb_agg_y = C("wb_agg_y"); wb_sum_y = C("wb_sum_y")
    wb_agg_m = C("wb_agg_m"); wb_sum_m = C("wb_sum_m")
    if wb_agg_m is None:
        st.warning("Нет данных. Обновите кэш.")
        st.stop()

    wy = _wb(wb_sum_y, wb_agg_y)
    wm = _wb(wb_sum_m, wb_agg_m)

    # ── 3 cards: marketplace | yesterday | month (lovable-ref style) ──
    def _info_card_wb(html, pill_color="#8B5CF6"):
        st.markdown(
            f'<div class="card-matte" style="height:100%">'
            f'<div style="display:flex;gap:16px;padding:24px;align-items:stretch">'
            f'<div style="width:4px;border-radius:4px;background:{pill_color};flex-shrink:0;align-self:stretch"></div>'
            f'<div style="flex:1">{html}</div>'
            f'</div></div>',
            unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _info_card_wb(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#8B5CF6;margin-bottom:8px">WILDBERRIES</div>'
            f'<div style="font-size:12px;color:#64748b">SKU с продажами (месяц)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#1e293b;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{wm["sku"]}</div>'
            f'<div style="font-size:11px;color:#64748b;margin-top:8px">{MSTR}</div>'
        )
    with c2:
        ym_color = "#059669" if wy["margin"] >= 20 else "#ca8a04" if wy["margin"] >= 15 else "#dc2626"
        yp_color = "#059669" if wy["profit"] >= 0 else "#dc2626"
        _info_card_wb(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#8B5CF6;margin-bottom:8px">ВЧЕРА</div>'
            f'<div style="font-size:32px;font-weight:700;color:{ym_color};line-height:1.2;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{wy["margin"]:.1f}%</div>'
            f'<div style="font-size:11px;color:#64748b;margin-bottom:12px">маржа</div>'
            f'<div style="display:flex;gap:24px">'
            f'<div><div style="font-size:11px;color:#64748b">К выплате</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:#1e293b">{_fm(wy["rev"])} ₽</div></div>'
            f'<div><div style="font-size:11px;color:#64748b">Прибыль</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:{yp_color}">{_fm(wy["profit"])} ₽</div></div>'
            f'</div>'
        )
    with c3:
        mm_color = "#059669" if wm["margin"] >= 20 else "#ca8a04" if wm["margin"] >= 15 else "#dc2626"
        mp_color = "#059669" if wm["profit"] >= 0 else "#dc2626"
        _info_card_wb(
            f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;'
            f'color:#8B5CF6;margin-bottom:8px">МЕСЯЦ</div>'
            f'<div style="font-size:32px;font-weight:700;color:{mm_color};line-height:1.2;'
            f'font-feature-settings:\'tnum\' on;letter-spacing:-0.04em">{wm["margin"]:.1f}%</div>'
            f'<div style="font-size:11px;color:#64748b;margin-bottom:12px">маржа</div>'
            f'<div style="display:flex;gap:24px">'
            f'<div><div style="font-size:11px;color:#64748b">К выплате</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:#1e293b">{_fm(wm["rev"])} ₽</div></div>'
            f'<div><div style="font-size:11px;color:#64748b">Прибыль</div>'
            f'<div class="metric-value" style="font-size:14px;font-weight:600;color:{mp_color}">{_fm(wm["profit"])} ₽</div></div>'
            f'</div>'
        )

    # ── Period radio ──
    wb_period = st.radio("Период", ["Месяц", "Вчера"], horizontal=True, key="wb_period")
    _wb_df_key = "wb_df_m" if wb_period == "Месяц" else "wb_df_y"
    _wb_sum_key = "wb_sum_m" if wb_period == "Месяц" else "wb_sum_y"
    _wb_agg_key = "wb_agg_m" if wb_period == "Месяц" else "wb_agg_y"
    wb_df_cur = C(_wb_df_key)
    wb_sum_cur = C(_wb_sum_key)
    wb_agg_cur = C(_wb_agg_key)
    w_cur = _wb(wb_sum_cur, wb_agg_cur)

    # ── Donut: expense structure ──
    if wb_df_cur is not None and not wb_df_cur.empty:
        expenses = {}
        for col, lbl in [
            ("delivery_rub", "Логистика"),
            ("storage_fee", "Хранение"),
            ("deduction", "Удержания"),
            ("penalty", "Штрафы"),
            ("rebill_logistic_cost", "Обратная логистика"),
        ]:
            if col in wb_df_cur.columns:
                val = wb_df_cur[col].sum()
                if val > 0:
                    expenses[lbl] = val
        if wb_sum_cur:
            if wb_sum_cur.get("loyal_cost", 0) > 0:
                expenses["Лояльность (стоимость)"] = wb_sum_cur["loyal_cost"]
            if wb_sum_cur.get("loyal_balls", 0) > 0:
                expenses["Баллы лояльности"] = wb_sum_cur["loyal_balls"]

        if expenses:
            rows = sorted(expenses.items(), key=lambda x: x[1], reverse=True)
            total = sum(v for _, v in rows)
            rev = w_cur["vyr"] if w_cur["vyr"] else w_cur["rev"]
            exp_df = pd.DataFrame(rows, columns=["Тип расхода", "Сумма"])

            # Top-5 + "Прочие" for donut
            top5 = exp_df.head(5).copy()
            rest = exp_df.iloc[5:]
            if not rest.empty:
                other = pd.DataFrame([{"Тип расхода": "Прочие", "Сумма": rest["Сумма"].sum()}])
                donut_df = pd.concat([top5, other], ignore_index=True)
            else:
                donut_df = top5
            donut_df["label"] = donut_df.apply(
                lambda r: f'{r["Тип расхода"]}<br>{_fm(r["Сумма"])} ₽', axis=1)
            fig = px.pie(donut_df, values="Сумма", names="Тип расхода",
                         hole=0.55, color_discrete_sequence=px.colors.qualitative.Set2)
            fig.update_traces(textposition="outside", textinfo="label+value",
                              text=donut_df["label"], texttemplate="%{text}",
                              pull=[0.02] * len(donut_df))
            fig.update_layout(height=420, margin=dict(t=30, b=10, l=80, r=80),
                              title="Структура расходов", showlegend=False,
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True, key="wb_donut",
                            config={"displayModeBar": False})

            tbl_exp = pd.DataFrame({
                "Тип расхода": [r[0] for r in rows] + ["Итого"],
                "Сумма ₽": [r[1] for r in rows] + [total],
            })
            st.dataframe(
                tbl_exp.style.format({"Сумма ₽": "{:,.0f}"})
                .apply(lambda x: ["font-weight:bold" if x["Тип расхода"] == "Итого" else "" for _ in x], axis=1),
                use_container_width=True, hide_index=True,
            )

    # ── Юнит-экономика по SKU ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#8B5CF6"></span>ЮНИТ-ЭКОНОМИКА ПО SKU</div>', unsafe_allow_html=True)

    wb_unit_data = wb_agg_cur
    if wb_unit_data is not None and not wb_unit_data.empty:
        fc = st.columns([3, 1, 1, 1, 1])
        with fc[0]:
            q = st.text_input("Поиск", key="wb_q", placeholder="Название...",
                              label_visibility="collapsed")
        with fc[1]:
            mr_from = st.number_input("Маржа от %", value=-50, step=1, key="wb_mr_from")
        with fc[2]:
            mr_to = st.number_input("Маржа до %", value=80, step=1, key="wb_mr_to")
        with fc[3]:
            mq = st.number_input("Мин шт", 0, step=1, key="wb_mq")
        with fc[4]:
            df_ = st.selectbox("ДРР", ["Все", "С ДРР", "Без ДРР"], key="wb_df")

        tbl = wb_unit_data[["sa_name", "Наименование", "nm_id",
                       "qty", "vyruchka", "sebes_total", "adv_sum",
                       "profit_with_drr", "margin", "margin_with_drr"]].copy()
        tbl.columns = ["Артикул", "Название", "nm_id",
                        "Шт", "Выручка", "Себестоимость",
                        "ДРР", "Прибыль", "Маржа %", "Маржа с ДРР %"]
        tbl["Название"] = tbl["Название"].fillna(tbl["Артикул"])
        if q:
            mask = (tbl["Название"].str.contains(q, case=False, na=False) |
                    tbl["Артикул"].str.contains(q, case=False, na=False))
            tbl = tbl[mask]
        tbl = tbl[(tbl["Маржа с ДРР %"] >= mr_from) & (tbl["Маржа с ДРР %"] <= mr_to)]
        if mq > 0:
            tbl = tbl[tbl["Шт"] >= mq]
        if df_ == "С ДРР":
            tbl = tbl[tbl["ДРР"] > 0]
        elif df_ == "Без ДРР":
            tbl = tbl[tbl["ДРР"] == 0]
        tbl = tbl.sort_values("Выручка", ascending=False).reset_index(drop=True)

        fmt = {"Выручка": "{:,.0f}", "Себестоимость": "{:,.0f}",
               "ДРР": "{:,.0f}", "Прибыль": "{:,.0f}",
               "Маржа %": "{:.1f}%", "Маржа с ДРР %": "{:.1f}%",
               "Шт": "{:.0f}"}
        styled = (tbl.style.format(fmt)
                  .map(_margin_bar, subset=["Маржа %", "Маржа с ДРР %"]))
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} из {len(wb_unit_data)} SKU")

    # ── Анализ ДРР ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#8B5CF6"></span>АНАЛИЗ ДРР</div>', unsafe_allow_html=True)

    wb_drr_period = st.radio("Период", ["Месяц", "Вчера"], horizontal=True, key="wb_drr_period")
    wb_drr_src = C("wb_agg_m") if wb_drr_period == "Месяц" else C("wb_agg_y")

    if wb_drr_src is not None and not wb_drr_src.empty and "adv_sum" in wb_drr_src.columns:
        drr_data = wb_drr_src[wb_drr_src["adv_sum"] > 0].copy()
        total_drr = wb_drr_src["adv_sum"].sum()
        total_rev = wb_drr_src["vyruchka"].sum()
        drr_pct = (total_drr / total_rev * 100) if total_rev else 0

        dc1, dc2, dc3 = st.columns(3)
        dc1.metric("Общий ДРР", f"{_fm(total_drr)} ₽")
        dc2.metric("ДРР % от выручки", f"{drr_pct:.1f}%")
        dc3.metric("SKU с рекламой", str(len(drr_data)))

        if not drr_data.empty:
            drr_tbl = drr_data[["Наименование", "sa_name", "adv_sum", "vyruchka",
                                "margin", "margin_with_drr"]].copy()
            drr_tbl["Наименование"] = drr_tbl["Наименование"].fillna(drr_tbl["sa_name"])
            drr_tbl["ДРР %"] = (drr_tbl["adv_sum"] / drr_tbl["vyruchka"].replace(0, 0.001) * 100).round(1)
            drr_tbl = drr_tbl.rename(columns={
                "Наименование": "Название", "sa_name": "Артикул",
                "adv_sum": "Расход ₽", "vyruchka": "Выручка ₽",
                "margin": "Маржа без ДРР %", "margin_with_drr": "Маржа с ДРР %",
            })
            drr_tbl = drr_tbl.sort_values("Расход ₽", ascending=False).reset_index(drop=True)

            def _drr_color_wb(val):
                try: v = float(val)
                except: return ""
                if v > 10: return "background-color:rgba(220,38,38,0.15);color:#dc2626"
                if v >= 5: return "background-color:rgba(202,138,4,0.15);color:#ca8a04"
                return ""

            drr_fmt = {"Расход ₽": "{:,.0f}", "Выручка ₽": "{:,.0f}",
                       "ДРР %": "{:.1f}%", "Маржа без ДРР %": "{:.1f}%",
                       "Маржа с ДРР %": "{:.1f}%"}
            st.dataframe(
                drr_tbl.style.format(drr_fmt)
                .map(_drr_color_wb, subset=["ДРР %"])
                .map(_margin_bar, subset=["Маржа без ДРР %", "Маржа с ДРР %"]),
                use_container_width=True, hide_index=True, height=500,
            )

            # ── Bar chart: top-15 by revenue, DRR overlay ──
            chart_df = wb_drr_src[["Наименование", "sa_name", "vyruchka", "adv_sum", "margin_with_drr"]].copy()
            chart_df["Наименование"] = chart_df["Наименование"].fillna(chart_df["sa_name"])
            chart_df = chart_df.sort_values("vyruchka", ascending=False).head(15)
            chart_df = chart_df.sort_values("vyruchka", ascending=True)
            chart_df["ДРР %"] = (chart_df["adv_sum"] / chart_df["vyruchka"].replace(0, 0.001) * 100).round(1)
            chart_df["short"] = chart_df["Наименование"].str[:40]

            fig_drr = go.Figure()
            fig_drr.add_trace(go.Bar(
                y=chart_df["short"], x=chart_df["vyruchka"],
                orientation="h", name="Выручка",
                marker_color="#8B5CF6",
                customdata=chart_df[["Наименование", "vyruchka", "adv_sum", "ДРР %", "margin_with_drr"]].values,
                hovertemplate="<b>%{customdata[0]}</b><br>Выручка: %{customdata[1]:,.0f} ₽<br>"
                              "ДРР: %{customdata[2]:,.0f} ₽<br>ДРР: %{customdata[3]:.1f}%<br>"
                              "Маржа с ДРР: %{customdata[4]:.1f}%<extra></extra>",
            ))
            fig_drr.add_trace(go.Bar(
                y=chart_df["short"], x=chart_df["adv_sum"],
                orientation="h", name="ДРР",
                marker_color="#dc2626",
                hoverinfo="skip",
            ))
            fig_drr.update_layout(
                barmode="overlay", height=max(350, len(chart_df) * 28),
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                yaxis=dict(showgrid=False),
            )
            st.plotly_chart(fig_drr, use_container_width=True, key="wb_drr_chart",
                            config={"displayModeBar": False})
    else:
        st.info("Нет данных по ДРР за выбранный период")


# ══════════════════════════════════════════════════════════════════════════
# 📦 Остатки
# ══════════════════════════════════════════════════════════════════════════

elif page == "📦 Остатки":
    st.title("Остатки и оборачиваемость")
    st.caption("Ozon FBO")

    stock_df = C("stock_turnover")
    if stock_df is None or stock_df.empty:
        st.warning("Нет данных по остаткам. Обновите кэш.")
        st.stop()

    total_sku = stock_df["sku"].nunique()
    total_stock = stock_df["stock"].sum()
    avg_days = stock_df.groupby("sku")["days_of_stock"].min().median()
    low_count = stock_df[stock_df["days_of_stock"] <= 14]["sku"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKU", str(total_sku))
    c2.metric("Всего шт", _fm(total_stock))
    c3.metric("Медиана дней", f"{avg_days:.0f}")
    c4.metric("Менее 14d", str(low_count))

    st.subheader("По кластерам")
    clusters = stock_df.groupby("cluster").agg(
        sku_count=("sku", "nunique"),
        total_stock=("stock", "sum"),
        avg_days=("days_of_stock", "median"),
    ).reset_index().sort_values("avg_days")

    for _, cl in clusters.iterrows():
        label = cl["cluster"]
        d = cl["avg_days"]
        with st.expander(f"{label} — {cl['sku_count']} SKU, "
                         f"{int(cl['total_stock'])} шт, медиана {d:.0f}d"):
            sub = stock_df[stock_df["cluster"] == label].copy()
            sub = sub.sort_values("days_of_stock").reset_index(drop=True)
            cols = ["sku", "name", "warehouse", "stock", "daily_sales", "days_of_stock"]
            fmt_s = {"stock": "{:.0f}", "daily_sales": "{:.2f}", "days_of_stock": "{:.0f}"}
            styled = sub[cols].style.format(fmt_s).map(_sc, subset=["days_of_stock"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

    with st.expander("Все SKU"):
        q = st.text_input("Поиск", key="stock_q", placeholder="SKU или название...",
                          label_visibility="collapsed")
        tbl = stock_df.copy()
        if q:
            tbl = tbl[tbl["name"].str.contains(q, case=False, na=False) |
                       tbl["sku"].astype(str).str.contains(q)]
        tbl = tbl.sort_values("days_of_stock").reset_index(drop=True)
        fmt_s = {"stock": "{:.0f}", "daily_sales": "{:.2f}", "days_of_stock": "{:.0f}"}
        styled = tbl.style.format(fmt_s).map(_sc, subset=["days_of_stock"])
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)


# ══════════════════════════════════════════════════════════════════════════
# 🚛 Поставки
# ══════════════════════════════════════════════════════════════════════════

elif page == "🚛 Поставки":
    st.title("Планирование поставок")

    mp_choice = st.radio("Маркетплейс", ["Ozon", "WB"], horizontal=True, key="supply_mp")

    def _cluster_status(days):
        if days < 14: return "СРОЧНО"
        if days < 20: return "Скоро закончится"
        if days < 30: return "Внимание"
        return "Норма"

    def _cluster_color(val):
        if val == "СРОЧНО":
            return "background-color:rgba(220,38,38,0.15);color:#dc2626;font-weight:700"
        if val == "Скоро закончится":
            return "background-color:rgba(234,88,12,0.12);color:#ea580c;font-weight:600"
        if val == "Внимание":
            return "background-color:rgba(202,138,4,0.12);color:#ca8a04;font-weight:600"
        return "background-color:rgba(22,163,74,0.1);color:#16a34a"

    if mp_choice == "WB":
        st.caption("Wildberries FBO")

        wpc1, wpc2 = st.columns([1, 3])
        with wpc1:
            wb_depth = st.number_input("Глубина поставки, дней", min_value=14, max_value=180,
                                       value=60, step=7, key="wb_supply_depth")
        with wpc2:
            st.write("")
            st.write("")
            wb_calc_btn = st.button("Рассчитать", type="primary", key="wb_supply_calc")

        if wb_calc_btn:
            with st.spinner("Загрузка данных с WB и расчёт..."):
                from wb_supply import compute_wb_supply_data
                wb_result = compute_wb_supply_data(days_plan=wb_depth)
                st.session_state["wb_supply_result"] = wb_result
                st.session_state["wb_supply_depth_used"] = wb_depth

        wb_result = st.session_state.get("wb_supply_result")
        if wb_result is None:
            st.info("Нажмите «Рассчитать» для загрузки данных")
            st.stop()

        wb_depth_used = st.session_state.get("wb_supply_depth_used", 60)
        wb_cp = wb_result["cluster_priority"]
        wb_plan = wb_result["plan"]

        # Справочная строка
        wb_total_order = int(wb_plan["order"].sum())
        wb_total_price = (wb_plan["order"] * wb_plan["price"].fillna(0)).sum()
        wb_total_sku = wb_plan[wb_plan["order"] > 0]["article"].nunique()
        st.markdown(
            f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;'
            f'padding:14px 20px;margin:16px 0;font-size:15px;font-weight:600;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04)">'
            f'К отгрузке: <b>{_fm(wb_total_order)}</b> шт · '
            f'<b>{_fm(wb_total_price)}</b> ₽ · '
            f'<b>{wb_total_sku}</b> SKU · '
            f'Глубина: <b>{wb_depth_used}</b> дней</div>',
            unsafe_allow_html=True,
        )

        # Приоритет отгрузки
        st.markdown(
            '<div class="section-label"><span class="section-dot" style="background:#8B5CF6"></span>'
            'ПРИОРИТЕТ ОТГРУЗКИ</div>', unsafe_allow_html=True)
        st.caption("Кластеры с минимальным запасом — грузим первыми")

        wb_cp_disp = wb_cp[["Кластер", "Дней запаса", "Заказать"]].copy()
        wb_cp_disp["Дней запаса"] = wb_cp_disp["Дней запаса"].astype(int)
        wb_cp_disp["Заказать"] = wb_cp_disp["Заказать"].astype(int)
        wb_cp_disp["Статус"] = wb_cp_disp["Дней запаса"].apply(_cluster_status)

        with st.container(border=True):
            st.dataframe(
                wb_cp_disp.style
                .format({"Заказать": "{:,.0f}"})
                .map(_cluster_color, subset=["Статус"])
                .map(_sc, subset=["Дней запаса"]),
                use_container_width=True, hide_index=True,
            )

        # Раскрытие по кластерам
        for _, row in wb_cp_disp.iterrows():
            cl_name = row["Кластер"]
            cl_plan = wb_plan[wb_plan["cluster"] == cl_name].copy()
            cl_plan = cl_plan[cl_plan["order"] > 0].sort_values("order", ascending=False)
            if cl_plan.empty:
                continue
            with st.expander(f"{cl_name} — {int(row['Дней запаса'])}д, заказать {int(row['Заказать'])} шт"):
                ct = cl_plan[["name", "article", "stock", "daily", "days", "order"]].copy()
                ct.columns = ["Название", "Артикул", "Остаток", "Прод/день", "Дней", "Заказать"]
                ct["Остаток"] = ct["Остаток"].astype(int)
                ct["Дней"] = ct["Дней"].astype(int)
                ct["Заказать"] = ct["Заказать"].astype(int)
                st.dataframe(
                    ct.style.format({"Прод/день": "{:.1f}"}).map(_sc, subset=["Дней"]),
                    use_container_width=True, hide_index=True,
                )

        # ── Скачать Excel ──
        st.divider()
        import io
        wb_buf = io.BytesIO()
        wb_export = wb_plan[wb_plan["order"] > 0][
            ["name", "article", "cluster", "stock", "daily", "days", "need", "quant", "order", "price"]
        ].copy()
        wb_export.columns = ["Название", "Артикул", "Кластер", "Остаток", "Прод/день",
                             "Дней запаса", "Потребность", "Квант", "Заказать", "Цена"]
        wb_export.to_excel(wb_buf, index=False, sheet_name="План WB")
        st.download_button(
            "Скачать план поставок (Excel)",
            data=wb_buf.getvalue(),
            file_name=f"wb_supply_plan_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="wb_supply_download",
        )

        st.stop()

    # ── Ozon ──
    st.caption("Ozon FBO")

    pc1, pc2 = st.columns([1, 3])
    with pc1:
        days_depth = st.number_input("Глубина поставки, дней", min_value=14, max_value=180,
                                     value=60, step=7, key="supply_depth")
    with pc2:
        st.write("")  # spacer
        st.write("")
        calc_btn = st.button("Рассчитать", type="primary", key="supply_calc")

    # Run calculation on button click or use cached result
    if calc_btn:
        with st.spinner("Загрузка данных с Ozon и расчёт..."):
            from ozon_supply import compute_supply_data
            result = compute_supply_data(days_plan=days_depth)
            st.session_state["supply_result"] = result
            st.session_state["supply_depth_used"] = days_depth

    result = st.session_state.get("supply_result")
    if result is None:
        # Show basic view from cache
        stock_df = C("stock_turnover")
        if stock_df is None or stock_df.empty:
            st.info("Нажмите «Рассчитать» для загрузки данных")
            st.stop()

        # Quick cluster summary from cache
        cl = stock_df.groupby("cluster").agg(
            stock=("stock", "sum"), daily=("daily_sales", "sum"),
        ).reset_index()
        cl["days"] = (cl["stock"] / cl["daily"].replace(0, 0.001)).round(0).clip(upper=999)
        cl = cl.sort_values("days")
        total_sku = stock_df["sku"].nunique()
        st.caption(f"Данные из кэша. Нажмите «Рассчитать» для полного анализа с магистралью.")
        st.metric("SKU на складах", str(total_sku))
        st.stop()

    depth_used = st.session_state.get("supply_depth_used", 60)
    cp = result["cluster_priority"]
    mag = result["magistral"]
    plan = result["plan"]
    sales_df = result.get("sales")

    # ── Справочная строка ──
    total_order = int(plan["order"].sum())
    total_price = (plan["order"] * plan["price"].fillna(0)).sum()
    total_sku = plan[plan["order"] > 0]["sku"].nunique()
    st.markdown(
        f'<div class="card-matte" style="padding:14px 20px;margin:16px 0;font-size:15px;font-weight:600">'
        f'К отгрузке: <b>{_fm(total_order)}</b> шт · '
        f'<b>{_fm(total_price)}</b> ₽ · '
        f'<b>{total_sku}</b> SKU · '
        f'Глубина: <b>{depth_used}</b> дней</div>',
        unsafe_allow_html=True,
    )

    # ── Заказы по кластерам (свёрнуто) ──
    cp_disp = cp[["Кластер", "Дней запаса", "Заказать"]].copy()
    cp_disp["Дней запаса"] = cp_disp["Дней запаса"].astype(int)
    cp_disp["Заказать"] = cp_disp["Заказать"].astype(int)
    cp_disp["Статус"] = cp_disp["Дней запаса"].apply(_cluster_status)

    with st.expander("Заказы по кластерам (развернуть)"):
        st.dataframe(
            cp_disp.style
            .format({"Заказать": "{:,.0f}"})
            .map(_cluster_color, subset=["Статус"])
            .map(_sc, subset=["Дней запаса"]),
            use_container_width=True, hide_index=True,
        )
        for _, row in cp_disp.iterrows():
            cl_name = row["Кластер"]
            cl_plan = plan[plan["cluster"] == cl_name].copy()
            cl_plan = cl_plan[cl_plan["order"] > 0].sort_values("order", ascending=False)
            if cl_plan.empty:
                continue
            st.markdown(f"**{cl_name}** — {int(row['Дней запаса'])}д, заказать {int(row['Заказать'])} шт")
            ct = cl_plan[["name", "sku", "stock_total", "daily", "days", "order"]].copy()
            ct.columns = ["Название", "SKU", "Остаток", "Прод/день", "Дней", "Заказать"]
            ct["Остаток"] = ct["Остаток"].astype(int)
            ct["Дней"] = ct["Дней"].astype(int)
            ct["Заказать"] = ct["Заказать"].astype(int)
            st.dataframe(
                ct.style.format({"Прод/день": "{:.1f}"}).map(_sc, subset=["Дней"]),
                use_container_width=True, hide_index=True,
            )

    # ── Анализ магистрали ──
    if mag is not None and not mag.empty:
        st.divider()
        st.markdown(
            '<div class="section-label"><span class="section-dot" style="background:#8B5CF6"></span>'
            'АНАЛИЗ МАГИСТРАЛИ</div>', unsafe_allow_html=True)

        # ── Build full magistral picture from sales data ──
        mag_flows = pd.DataFrame()
        mag_full = pd.DataFrame()
        if sales_df is not None and not sales_df.empty:
            mag_flows = sales_df[sales_df["wh_cluster"] != sales_df["dest_cluster"]].copy()

            all_clusters = sorted(set(sales_df["wh_cluster"].unique()) | set(sales_df["dest_cluster"].unique()))
            sent = mag_flows.groupby("wh_cluster")["quantity"].sum() if not mag_flows.empty else pd.Series(dtype=float)
            received = mag_flows.groupby("dest_cluster")["quantity"].sum() if not mag_flows.empty else pd.Series(dtype=float)

            mag_rows = []
            for cl in all_clusters:
                snt = int(sent.get(cl, 0))
                rcv = int(received.get(cl, 0))
                bal = rcv - snt
                mag_rows.append({"cluster": cl, "sent": snt, "received": rcv, "balance": bal})
            mag_full = pd.DataFrame(mag_rows).sort_values("balance")

        if not mag_full.empty:
            vampires = mag_full[mag_full["balance"] > 0].sort_values("balance", ascending=False)
            hamsters = mag_full[mag_full["balance"] < 0].sort_values("balance")

            # ── 2 columns: vampires | hamsters ──
            mc1, mc2 = st.columns(2)
            with mc1:
                with st.container(border=True):
                    st.markdown(f"**🧛 Вампиры** ({len(vampires)})")
                    st.caption("Забирают больше чем отдают")
                    for _, r in vampires.head(5).iterrows():
                        st.markdown(f"**{r['cluster']}** — +{r['balance']} шт")
                    if len(vampires) > 5:
                        with st.expander(f"Показать все (ещё {len(vampires) - 5})"):
                            for _, r in vampires.iloc[5:].iterrows():
                                st.markdown(f"**{r['cluster']}** — +{r['balance']} шт")
            with mc2:
                with st.container(border=True):
                    st.markdown(f"**🐹 Хомяки** ({len(hamsters)})")
                    st.caption("Отдают больше чем забирают")
                    for _, r in hamsters.head(5).iterrows():
                        st.markdown(f"**{r['cluster']}** — {r['balance']} шт")
                    if len(hamsters) > 5:
                        with st.expander(f"Показать все (ещё {len(hamsters) - 5})"):
                            for _, r in hamsters.iloc[5:].iterrows():
                                st.markdown(f"**{r['cluster']}** — {r['balance']} шт")

            # ── Selectbox for detail ──
            all_mag_clusters = mag_full[mag_full["balance"] != 0]["cluster"].tolist()
            if all_mag_clusters and not mag_flows.empty:
                st.markdown("")
                sel_cl = st.selectbox(
                    "Выберите кластер для детализации",
                    [""] + all_mag_clusters,
                    format_func=lambda x: "—" if x == "" else x,
                    key="mag_detail_cluster",
                )

                if sel_cl:
                    incoming = mag_flows[mag_flows["dest_cluster"] == sel_cl]
                    outgoing = mag_flows[mag_flows["wh_cluster"] == sel_cl]

                    dc1, dc2 = st.columns(2)

                    # Left: receives from
                    with dc1:
                        with st.container(border=True):
                            total_in = int(incoming["quantity"].sum()) if not incoming.empty else 0
                            st.markdown(f"**Забирает из** — {total_in} шт")
                            if not incoming.empty:
                                by_src = incoming.groupby("wh_cluster")["quantity"].sum().reset_index()
                                by_src.columns = ["cluster", "qty"]
                                by_src = by_src.sort_values("qty", ascending=False)
                                for _, sr in by_src.iterrows():
                                    src_cl = sr["cluster"]
                                    src_qty = int(sr["qty"])
                                    with st.expander(f"{src_cl} — {src_qty} шт"):
                                        skus = incoming[incoming["wh_cluster"] == src_cl].groupby(
                                            ["sku", "name"])["quantity"].sum().reset_index()
                                        skus.columns = ["SKU", "Название", "Шт"]
                                        skus["SKU"] = skus["SKU"].astype(str).str.replace(".0", "", regex=False)
                                        skus["Шт"] = skus["Шт"].astype(int)
                                        skus = skus.sort_values("Шт", ascending=False).reset_index(drop=True)
                                        st.dataframe(skus, use_container_width=True, hide_index=True)
                            else:
                                st.caption("Не забирает ни у кого")

                    # Right: sends to
                    with dc2:
                        with st.container(border=True):
                            total_out = int(outgoing["quantity"].sum()) if not outgoing.empty else 0
                            st.markdown(f"**Отдаёт в** — {total_out} шт")
                            if not outgoing.empty:
                                by_dst = outgoing.groupby("dest_cluster")["quantity"].sum().reset_index()
                                by_dst.columns = ["cluster", "qty"]
                                by_dst = by_dst.sort_values("qty", ascending=False)
                                for _, dr in by_dst.iterrows():
                                    dst_cl = dr["cluster"]
                                    dst_qty = int(dr["qty"])
                                    with st.expander(f"{dst_cl} — {dst_qty} шт"):
                                        skus = outgoing[outgoing["dest_cluster"] == dst_cl].groupby(
                                            ["sku", "name"])["quantity"].sum().reset_index()
                                        skus.columns = ["SKU", "Название", "Шт"]
                                        skus["SKU"] = skus["SKU"].astype(str).str.replace(".0", "", regex=False)
                                        skus["Шт"] = skus["Шт"].astype(int)
                                        skus = skus.sort_values("Шт", ascending=False).reset_index(drop=True)
                                        st.dataframe(skus, use_container_width=True, hide_index=True)
                            else:
                                st.caption("Не отдаёт никому")

    # ── Скачать Excel ──
    st.divider()
    if st.button("Скачать полный план (Excel)", key="supply_download"):
        with st.spinner("Формируется Excel..."):
            from ozon_supply import build_supply_plan
            fname = build_supply_plan(days_plan=depth_used)
        st.success(f"Сохранено: {fname}")


# ══════════════════════════════════════════════════════════════════════════
# 💰 Цены
# ══════════════════════════════════════════════════════════════════════════

elif page == "💰 Цены":
    st.title("Цены")

    price_tab = st.radio("", ["Ozon", "WB"], horizontal=True, key="price_tab")

    # Общие данные
    from utils.price_loader import load_price, build_cost_map
    price_df = C("price")
    if price_df is None:
        price_df = load_price(PRICE_FILE)
    name_map = dict(zip(
        price_df["Артикул"].astype(str).str.strip(),
        price_df["Наименование"],
    ))

    # ══════════════════════════════════════════════════════════════════
    if price_tab == "Ozon":
        st.caption("Ozon Seller API · /v5/product/info/prices · только товары с продажами")
        oz_prices = C("oz_prices")
        if oz_prices is None or oz_prices.empty:
            st.warning("Нет данных. Обновите кэш.")
            st.stop()

        disp = oz_prices[["offer_id", "name", "price", "old_price"]].copy()
        disp.columns = ["Артикул", "Название", "Цена", "Цена до скидки"]
        disp = disp.sort_values("Цена", ascending=False).reset_index(drop=True)

        c1, c2 = st.columns(2)
        c1.metric("Товаров", str(len(disp)))
        avg_disc = ((disp["Цена до скидки"] - disp["Цена"]) / disp["Цена до скидки"] * 100)
        c2.metric("Ср. скидка", f"{avg_disc.mean():.0f}%")

        st.info("Ozon Seller API не отдаёт цену по Ozon Карте — это внутренняя скидка Ozon для покупателей.")

        q = st.text_input("Поиск", key="oz_price_q", placeholder="Артикул или название...",
                          label_visibility="collapsed")
        tbl = disp.copy()
        if q:
            mask = tbl["Артикул"].str.contains(q, case=False, na=False) | \
                   tbl["Название"].str.contains(q, case=False, na=False)
            tbl = tbl[mask]

        fmt = {"Цена": "{:.0f}", "Цена до скидки": "{:.0f}"}
        st.dataframe(tbl.style.format(fmt, na_rep="—"),
                     use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} товаров")

    # ══════════════════════════════════════════════════════════════════
    elif price_tab == "WB":
        st.caption("WB Discounts-Prices API · только товары с продажами")
        wb_prices = C("wb_prices")
        if wb_prices is None or wb_prices.empty:
            st.warning("Нет данных. Обновите кэш.")
            st.stop()

        disp = wb_prices[["vendorCode", "price", "discountedPrice",
                           "clubDiscountedPrice"]].copy()
        disp.columns = ["Артикул", "Цена до скидки", "Цена", "Цена с WB кошельком"]
        disp["Артикул"] = disp["Артикул"].astype(str).str.strip()
        disp["Название"] = disp["Артикул"].map(name_map).fillna("")
        disp = disp[["Артикул", "Название", "Цена до скидки", "Цена",
                      "Цена с WB кошельком"]]
        disp = disp.sort_values("Цена", ascending=False).reset_index(drop=True)

        c1, c2 = st.columns(2)
        c1.metric("Товаров", str(len(disp)))
        wallet_diff = (disp["Цена"] - disp["Цена с WB кошельком"]).sum()
        c2.metric("С доп. скидкой кошелька",
                  str((disp["Цена с WB кошельком"] < disp["Цена"]).sum()))

        q = st.text_input("Поиск", key="wb_price_q", placeholder="Артикул или название...",
                          label_visibility="collapsed")
        tbl = disp.copy()
        if q:
            mask = tbl["Артикул"].str.contains(q, case=False, na=False) | \
                   tbl["Название"].str.contains(q, case=False, na=False)
            tbl = tbl[mask]

        fmt = {"Цена до скидки": "{:.0f}", "Цена": "{:.0f}",
               "Цена с WB кошельком": "{:.0f}"}
        st.dataframe(tbl.style.format(fmt, na_rep="—"),
                     use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} товаров")


# ══════════════════════════════════════════════════════════════════════════
# 🎯 Акции
# ══════════════════════════════════════════════════════════════════════════

elif page == "🎯 Акции":
    st.title("Акции")

    promo_mp = st.radio("", ["Ozon", "WB"], horizontal=True, key="promo_tab")

    # Общие данные
    from utils.price_loader import load_price
    price_df = C("price")
    if price_df is None:
        price_df = load_price(PRICE_FILE)

    # ══════════════════════════════════════════════════════════════════
    if promo_mp == "Ozon":
        st.caption("Ozon Seller API · /v1/actions")

        oz_promos = C("oz_promos")
        if oz_promos is None:
            st.warning("Нет данных по акциям Ozon. Обновите кэш.")
            st.stop()

        actions = oz_promos.get("actions")
        details = oz_promos.get("details", {})

        if actions is None or actions.empty:
            st.info("Нет активных акций Ozon.")
            st.stop()

        # Метрики
        total_participating = actions["participating_products_count"].sum() if "participating_products_count" in actions.columns else 0
        total_potential = actions["potential_products_count"].sum() if "potential_products_count" in actions.columns else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Акций", str(len(actions)))
        c2.metric("Наших товаров в акциях", _fm(total_participating))
        c3.metric("Доступно для участия", _fm(total_potential))

        # Таблица акций
        disp_actions = actions[["id", "title", "date_start", "date_end",
                                 "participating_products_count", "potential_products_count"]].copy()
        disp_actions.columns = ["ID", "Название", "Начало", "Конец", "Участвуют", "Кандидаты"]
        for col in ["Начало", "Конец"]:
            disp_actions[col] = pd.to_datetime(disp_actions[col]).dt.strftime("%d.%m.%Y")

        st.markdown('<div class="section-label"><span class="section-dot" style="background:#005BFF"></span>Список акций</div>', unsafe_allow_html=True)
        st.dataframe(disp_actions, use_container_width=True, hide_index=True)

        # Выбор акции для детализации
        action_options = {f"{row['title']} (#{row['id']})": int(row["id"])
                          for _, row in actions.iterrows()}
        selected_label = st.selectbox("Выберите акцию для детализации", list(action_options.keys()),
                                       key="oz_promo_select")
        selected_id = action_options[selected_label]

        promo_detail = details.get(selected_id, pd.DataFrame())
        if promo_detail.empty:
            st.info("Нет товаров в этой акции.")
        else:
            # Названия из прайса по offer_id (= Артикул)
            name_map = dict(zip(
                price_df["Артикул"].astype(str).str.strip(),
                price_df["Наименование"],
            ))
            # Маржа вчера из oz_final_y: sku → Маржа с учетом ДРР (%)
            # Цепочка: offer_id → (price file) → Ozon SKU ID → oz_final_y
            oz_final_y = C("oz_final_y")
            art_to_sku = dict(zip(
                price_df["Артикул"].astype(str).str.strip(),
                price_df["Ozon SKU ID"].dropna().astype(int).astype(str),
            ))
            sku_margin_map = {}
            if oz_final_y is not None and not oz_final_y.empty:
                sku_margin_map = dict(zip(
                    oz_final_y["sku"].astype(str),
                    oz_final_y["Маржа с учетом ДРР (%)"],
                ))

            if "offer_id" in promo_detail.columns:
                promo_detail["name"] = promo_detail["offer_id"].astype(str).str.strip().map(name_map).fillna("")
                promo_detail["_sku"] = promo_detail["offer_id"].astype(str).str.strip().map(art_to_sku).fillna("")
                promo_detail["margin_yesterday"] = promo_detail["_sku"].map(sku_margin_map)

            cols_to_show = []
            rename_map = {}

            if "offer_id" in promo_detail.columns:
                cols_to_show.append("offer_id")
                rename_map["offer_id"] = "Артикул"
            if "name" in promo_detail.columns:
                cols_to_show.append("name")
                rename_map["name"] = "Название"
            if "in_action" in promo_detail.columns:
                cols_to_show.append("in_action")
                rename_map["in_action"] = "Участвует"
            if "price" in promo_detail.columns:
                cols_to_show.append("price")
                rename_map["price"] = "Текущая цена"
            if "action_price" in promo_detail.columns:
                cols_to_show.append("action_price")
                rename_map["action_price"] = "Цена акции"
            if "max_action_price" in promo_detail.columns:
                cols_to_show.append("max_action_price")
                rename_map["max_action_price"] = "Макс. цена акции"
            if "margin_yesterday" in promo_detail.columns:
                cols_to_show.append("margin_yesterday")
                rename_map["margin_yesterday"] = "Маржа вчера %"

            tbl = promo_detail[cols_to_show].copy().rename(columns=rename_map)

            # Метрики по выбранной акции
            in_count = promo_detail["in_action"].sum() if "in_action" in promo_detail.columns else 0
            cand_count = len(promo_detail) - in_count
            c1, c2 = st.columns(2)
            c1.metric("Участвуют", str(int(in_count)))
            c2.metric("Кандидаты", str(int(cand_count)))

            # Фильтры
            filter_col = st.radio("Показать", ["Все", "Участвующие", "Кандидаты"],
                                   horizontal=True, key="oz_promo_filter")
            if filter_col == "Участвующие" and "Участвует" in tbl.columns:
                tbl = tbl[tbl["Участвует"] == True]
            elif filter_col == "Кандидаты" and "Участвует" in tbl.columns:
                tbl = tbl[tbl["Участвует"] == False]

            q = st.text_input("Поиск", key="oz_promo_q", placeholder="Артикул или название...",
                              label_visibility="collapsed")
            if q:
                mask = pd.Series(False, index=tbl.index)
                for col in ["Артикул", "Название"]:
                    if col in tbl.columns:
                        mask = mask | tbl[col].astype(str).str.contains(q, case=False, na=False)
                tbl = tbl[mask]

            # Формат
            fmt = {}
            for c in ["Текущая цена", "Цена акции", "Макс. цена акции"]:
                if c in tbl.columns:
                    fmt[c] = "{:.0f}"
            if "Маржа вчера %" in tbl.columns:
                fmt["Маржа вчера %"] = "{:.1f}%"

            styled = tbl.style.format(fmt, na_rep="—")
            if "Маржа вчера %" in tbl.columns:
                styled = styled.map(_mc, subset=["Маржа вчера %"])

            st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
            st.caption(f"{len(tbl)} товаров")

    # ══════════════════════════════════════════════════════════════════
    elif promo_mp == "WB":
        st.caption("WB Calendar API · dp-calendar-api.wildberries.ru")

        # Загрузка Excel автоакций из кабинета WB
        import os
        promo_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promo_data")
        with st.expander("Загрузить Excel автоакции из кабинета WB"):
            st.caption("Скачайте файл товаров автоакции из ЛК WB и загрузите сюда. "
                       "Имя файла: `<ID акции>_<название>.xlsx` (например `2116_Скидки.xlsx`).")
            uploaded = st.file_uploader("Excel файл автоакции", type=["xlsx"], key="wb_auto_promo_upload")
            if uploaded is not None:
                os.makedirs(promo_data_dir, exist_ok=True)
                save_path = os.path.join(promo_data_dir, uploaded.name)
                with open(save_path, "wb") as f:
                    f.write(uploaded.getbuffer())
                st.success(f"Сохранено: `promo_data/{uploaded.name}`")

                # Показать содержимое
                try:
                    from wb_promos import load_auto_promo_xlsx
                    xlsx_data = load_auto_promo_xlsx()
                    for pid, pdf in xlsx_data.items():
                        st.markdown(f"**Акция #{pid}**: {len(pdf)} товаров с planPrice")
                        st.dataframe(pdf.head(20), use_container_width=True, hide_index=True)
                except Exception as e:
                    st.error(f"Ошибка чтения файла: {e}")

            # Показать уже загруженные файлы
            if os.path.isdir(promo_data_dir):
                files = [f for f in os.listdir(promo_data_dir) if f.endswith(".xlsx")]
                if files:
                    st.caption(f"Файлы в promo_data/: {', '.join(sorted(files))}")

        dash = C("wb_promos_dash")
        if dash is None or dash.empty:
            st.warning("Нет данных по акциям WB. Обновите кэш.")
            st.stop()

        # Метрики
        c1, c2, c3 = st.columns(3)
        c1.metric("Товаров", str(len(dash)))
        avg_m = dash["Маржа сейчас %"].mean()
        c2.metric("Ср. маржа", f"{avg_m:.1f}%")
        total_s = dash["Остаток"].sum()
        c3.metric("Остаток", _fm(total_s))

        # Определяем колонки акций
        promo_cols = [c for c in dash.columns if c.startswith("Цена:")]
        if promo_cols:
            st.markdown(f'<div class="section-label"><span class="section-dot" style="background:#8B5CF6"></span>Акции ({len(promo_cols)})</div>', unsafe_allow_html=True)

            # Показываем сводку по акциям
            promo_summary = []
            for col in promo_cols:
                promo_name = col.replace("Цена: ", "")
                has_price = dash[col].apply(lambda x: isinstance(x, (int, float)) and x > 0)
                count = has_price.sum()
                if count > 0:
                    promo_summary.append({"Акция": promo_name, "Товаров с ценой": count})
                else:
                    # Может быть строка-информация для автоакций
                    info_vals = dash[col].dropna().unique()
                    info = info_vals[0] if len(info_vals) > 0 else "—"
                    promo_summary.append({"Акция": promo_name, "Товаров с ценой": str(info)})

            if promo_summary:
                st.dataframe(pd.DataFrame(promo_summary), use_container_width=True, hide_index=True)

        # Таблица товаров
        with st.expander("Товары и акции", expanded=True):
            q = st.text_input("Поиск", key="wbp_promo_q", placeholder="Артикул или название...",
                              label_visibility="collapsed")
            tbl = dash.copy()
            if q:
                mask = pd.Series(False, index=tbl.index)
                for col in ["Артикул", "Название"]:
                    if col in tbl.columns:
                        mask = mask | tbl[col].astype(str).str.contains(q, case=False, na=False)
                tbl = tbl[mask]

            # Рассчитываем маржу для акционных цен
            # Колонки "Цена:" могут содержать строки (автоакции без XLSX) — приводим к числу
            for col in promo_cols:
                tbl[col] = pd.to_numeric(tbl[col], errors="coerce")
                margin_col = col.replace("Цена:", "Маржа:")
                numeric_mask = tbl[col].notna()
                if numeric_mask.any():
                    price_vals = tbl[col]
                    cost_vals = tbl["Себестоимость"]
                    tbl[margin_col] = (
                        (price_vals - cost_vals) / price_vals * 100
                    ).where(price_vals > 0, None).round(1)

            fmt = {"Остаток": "{:.0f}", "Себестоимость": "{:.0f}",
                   "Цена сейчас": "{:.0f}", "Маржа сейчас %": "{:.1f}%"}
            for col in tbl.columns:
                if col.startswith("Цена:"):
                    fmt[col] = "{:.0f}"
                if col.startswith("Маржа:"):
                    fmt[col] = "{:.1f}%"

            margin_cols = ["Маржа сейчас %"] + [c for c in tbl.columns if c.startswith("Маржа:")]
            styled = tbl.style.format(fmt, na_rep="—")
            for mc_col in margin_cols:
                if mc_col in tbl.columns:
                    styled = styled.map(_mc, subset=[mc_col])

            st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
            st.caption(f"{len(tbl)} товаров")


# ══════════════════════════════════════════════════════════════════════════
# 💰 Цены WB (старая страница — акции)
# ══════════════════════════════════════════════════════════════════════════

elif page == "💰 Цены WB":
    st.title("Цены и акции WB")
    st.caption("Wildberries")

    dash = C("wb_promos_dash")
    if dash is None or dash.empty:
        st.warning("Нет данных. Обновите кэш.")
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("Товаров", str(len(dash)))
    avg_m = dash["Маржа сейчас %"].mean()
    c2.metric("Ср. маржа", f"{avg_m:.1f}%")
    total_s = dash["Остаток"].sum()
    c3.metric("Остаток", _fm(total_s))

    with st.expander("Товары и акции", expanded=True):
        q = st.text_input("Поиск", key="wbp_q", placeholder="Артикул или название...",
                          label_visibility="collapsed")
        tbl = dash.copy()
        if q:
            mask = pd.Series(False, index=tbl.index)
            for col in ["Артикул", "Название"]:
                if col in tbl.columns:
                    mask = mask | tbl[col].astype(str).str.contains(q, case=False, na=False)
            tbl = tbl[mask]

        fmt = {"Остаток": "{:.0f}", "Себестоимость": "{:.0f}",
               "Цена сейчас": "{:.0f}", "Маржа сейчас %": "{:.1f}%"}
        for col in tbl.columns:
            if col.startswith("Цена:") or col.startswith("Авто:"):
                fmt[col] = "{:.0f}"

        styled = tbl.style.format(fmt, na_rep="—")
        if "Маржа сейчас %" in tbl.columns:
            styled = styled.map(_mc, subset=["Маржа сейчас %"])
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} товаров")
