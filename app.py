"""
Alchemy dashboard — multi-page, cache-driven.
streamlit run app.py
"""

import streamlit as st
from db import init_db
from auth import is_authenticated, login_page, logout

init_db()

if not is_authenticated():
    login_page()
    st.stop()

# ── Authenticated — set up wide layout ─────────────────────────────────
st.set_page_config(page_title="Alchemy", layout="wide",
                   initial_sidebar_state="expanded")

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from data_loader import load as _dl_load, cache_age_minutes, cache_timestamp
from user_context import get_user_credentials, get_user_price_path

_USER_ID = st.session_state["user_id"]

# ── FastBoard BI global styles ────────────────────────────────────────────
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
    --bg: #f0f2f5; --card: #ffffff; --text: #1a1f36; --text-secondary: #6b7280;
    --border: #e5e7eb; --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02);
    --shadow-hover: 0 8px 25px rgba(0,0,0,0.08);
    --blue: #2563eb; --green: #059669; --purple: #7c3aed; --red: #dc2626;
}
.stApp, .stMarkdown, .stMetric, [data-testid="stMetricValue"] {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}
[data-testid="stMetricValue"], .mono-nums {
    font-family: 'JetBrains Mono', monospace !important;
    font-feature-settings: 'tnum' !important;
}
.section-label {
    font-size: 12px; font-weight: 700; letter-spacing: 1.2px;
    text-transform: uppercase; color: #6b7280;
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 8px; margin-top: 24px;
}
.section-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
/* Hide anchor link icons next to headers */
h1 a, h2 a, h3 a { display: none !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }
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
def _C(key, user_id):
    return _dl_load(key, user_id)

def C(key):
    """Load from per-user cache."""
    return _C(key, _USER_ID)

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
    # ДРР = реклама из advert-API + «WB Продвижение» (списано как удержание)
    drr_api = agg["adv_sum"].sum() if not agg.empty else 0
    drr_promo_extra = float(summary.get("wb_promo_extra", 0) or 0)
    drr = drr_api + drr_promo_extra
    vyr = agg["vyruchka"].sum() if not agg.empty else 0
    profit = rev - sebes - drr_api  # itogo уже за вычетом promo_extra, не вычитаем дважды
    # Маржа: % от "к выплате" (rev). Без себестоимости даёт 100%, что интуитивно.
    margin = (profit / rev * 100) if rev else 0
    return {"rev": rev, "sebes": sebes, "drr": drr, "profit": profit, "margin": margin, "vyr": vyr, "sku": len(agg)}

def _mc(val):
    """Margin color for dataframe styling: green >30%, teal 20-30%, yellow 15-20%, red <15%."""
    try: v = float(val)
    except: return ""
    if v >= 30: return "background-color:rgba(22,163,74,0.15);color:#16a34a"
    if v >= 20: return "background-color:rgba(6,182,212,0.15);color:#0891b2"
    if v >= 15: return "background-color:rgba(202,138,4,0.15);color:#ca8a04"
    return "background-color:rgba(220,38,38,0.15);color:#dc2626"

def _margin_bar(val):
    """Colored background + thin progress-bar at bottom of cell."""
    try: v = float(val)
    except: return ""
    w = max(0, min(100, v / 50 * 100))
    if v >= 30:
        bg = "rgba(22,163,74,0.15)"; tc = "#16a34a"; bc = "#34d399"
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

# ── AI helpers ────────────────────────────────────────────────────────────

import json as _json

_OZ_REGULATED_KEYWORDS = (
    "оплата за клик", "продвижение", "звёздные товары", "ускоренный сбор отзывов",
    "бонусы продавца", "подписка premium", "баллы за отзывы", "маркетинговые услуги",
    "внешнее продвижение",
)
_OZ_FIXED_KEYWORDS = ("эквайринг", "комиссия")

def _classify_oz_expense(name):
    n = name.lower()
    if any(k in n for k in _OZ_REGULATED_KEYWORDS):
        return "regulated"
    if any(k in n for k in _OZ_FIXED_KEYWORDS):
        return "fixed"
    return "semi_regulated"

_WB_GROUP_BY_LABEL = {
    "Лояльность": "regulated",
    "Баллы лояльности": "regulated",
    "WB Продвижение": "regulated",
    "Логистика": "semi_regulated",
    "Хранение": "semi_regulated",
    "Обратная логистика": "semi_regulated",
    "Удержания": "semi_regulated",
    "Штрафы": "semi_regulated",
}

def _group_expenses(items, classifier):
    """Сгруппировать словарь {статья: сумма} в три группы по управляемости."""
    groups = {
        "regulated": {"total": 0.0, "items": {}},
        "semi_regulated": {"total": 0.0, "items": {}},
        "fixed": {"total": 0.0, "items": {}},
    }
    for name, amount in items.items():
        grp = classifier(name)
        groups[grp]["items"][name] = amount
        groups[grp]["total"] += amount
    for g in groups.values():
        g["total"] = round(g["total"], 0)
    return groups


def _oz_expenses(nach_key):
    """Extract Ozon expenses dict from nach cache."""
    nach = C(nach_key)
    if nach is None or nach.empty:
        return {"total": 0, "items": {}, "groups": _group_expenses({}, _classify_oz_expense)}
    neg = nach[
        (nach["operation_type_name"] != "Общая сумма") &
        (nach["operation_type_name"] != "Доставка покупателю") &
        (nach["operation_type_name"] != "Доставка покупателю — отмена начисления") &
        (nach["amount"] < 0)
    ].copy()
    if neg.empty:
        return {"total": 0, "items": {}, "groups": _group_expenses({}, _classify_oz_expense)}
    neg["abs_amount"] = neg["amount"].abs()
    agg = neg.groupby("operation_type_name")["abs_amount"].sum().sort_values(ascending=False)
    items = {k: round(float(v), 0) for k, v in agg.items()}
    return {"total": round(float(agg.sum()), 0),
            "items": items,
            "groups": _group_expenses(items, _classify_oz_expense)}

def _wb_expenses(df_key, sum_key):
    """Extract WB expenses dict from df cache."""
    wb_df = C(df_key)
    if wb_df is None or wb_df.empty:
        return {"total": 0, "items": {}, "groups": _group_expenses({}, lambda n: _WB_GROUP_BY_LABEL.get(n, "semi_regulated"))}

    # Вычисляем «WB Продвижение» отдельно, остальное под «Удержания»
    wb_promo_extra = 0.0
    if "bonus_type_name" in wb_df.columns and "supplier_oper_name" in wb_df.columns:
        promo_mask = (
            (wb_df["supplier_oper_name"] == "Удержание")
            & wb_df["bonus_type_name"].fillna("").str.contains("WB Продвижение", case=False, na=False)
        )
        wb_promo_extra = float(wb_df.loc[promo_mask, "deduction"].sum())

    expenses = {}
    for col, lbl in [("delivery_rub", "Логистика"), ("storage_fee", "Хранение"),
                     ("deduction", "Удержания"), ("penalty", "Штрафы"),
                     ("rebill_logistic_cost", "Обратная логистика")]:
        if col in wb_df.columns:
            val = wb_df[col].sum()
            if col == "deduction":
                val = val - wb_promo_extra  # WB Продвижение выводим отдельной строкой
            if val > 0:
                expenses[lbl] = round(float(val), 0)
    if wb_promo_extra > 0:
        expenses["WB Продвижение"] = round(wb_promo_extra, 0)
    wb_sum = C(sum_key)
    if wb_sum:
        if wb_sum.get("loyal_cost", 0) > 0:
            expenses["Лояльность"] = round(float(wb_sum["loyal_cost"]), 0)
        if wb_sum.get("loyal_balls", 0) > 0:
            expenses["Баллы лояльности"] = round(float(wb_sum["loyal_balls"]), 0)
    return {"total": round(sum(expenses.values()), 0),
            "items": expenses,
            "groups": _group_expenses(expenses, lambda n: _WB_GROUP_BY_LABEL.get(n, "semi_regulated"))}

def _ym_ai_summary(d):
    """Extract YM summary for AI signal from cached data."""
    if d is None:
        return {"rev": 0, "profit": 0, "margin": 0}
    R = d["R"]
    totals = d["totals"]
    if R is None or R.empty:
        return {"rev": 0, "profit": 0, "margin": 0}
    rev = R["revenue"].sum()
    costs = sum(totals.values())
    sebes = R["sebes_total"].sum()
    profit = rev - costs - sebes
    margin = (profit / rev * 100) if rev else 0
    return {"rev": rev, "profit": profit, "margin": margin}

def _build_full_json():
    """Build full metrics JSON for AI signal (Сводка)."""
    oz_y = _oz(C("oz_final_y"), C("oz_nach_y"))
    wb_y = _wb(C("wb_sum_y"), C("wb_agg_y"))
    oz_m = _oz(C("oz_final_m"), C("oz_nach_m"))
    wb_m = _wb(C("wb_sum_m"), C("wb_agg_m"))
    ym_y_ai = _ym_ai_summary(C("ym_margin_y"))
    ym_m_ai = _ym_ai_summary(C("ym_margin_m"))
    t_y_rev = oz_y["rev"] + wb_y["rev"] + ym_y_ai["rev"]
    t_y_prf = oz_y["profit"] + wb_y["profit"] + ym_y_ai["profit"]
    t_m_rev = oz_m["rev"] + wb_m["rev"] + ym_m_ai["rev"]
    t_m_prf = oz_m["profit"] + wb_m["profit"] + ym_m_ai["profit"]

    def _s(d, with_drr=False):
        r = {"revenue": round(float(d["rev"]), 0), "profit": round(float(d["profit"]), 0),
             "margin_pct": round(float(d["margin"]), 1)}
        if with_drr:
            rev = d["rev"] if d["rev"] else 1
            r["drr_total"] = round(float(d["drr"]), 0)
            r["drr_pct"] = round(float(d["drr"]) / float(rev) * 100, 1)
        return r

    # Top-10 SKU
    oz_top, wb_top = [], []
    oz_f = C("oz_final_m")
    if oz_f is not None and not oz_f.empty:
        for _, r in oz_f.sort_values("Сумма отгрузки", ascending=False).head(10).iterrows():
            oz_top.append({"name": r["name"], "revenue": round(float(r["Сумма отгрузки"]), 0),
                           "margin_pct": round(float(r["Маржинальность (%)"]), 1),
                           "drr": round(float(r["ДРР"]), 0)})
    wb_a = C("wb_agg_m")
    if wb_a is not None and not wb_a.empty:
        for _, r in wb_a.sort_values("vyruchka", ascending=False).head(10).iterrows():
            nm = r["Наименование"] if pd.notna(r.get("Наименование")) else r["sa_name"]
            wb_top.append({"name": nm, "revenue": round(float(r["vyruchka"]), 0),
                           "margin_pct": round(float(r["margin"]), 1)})

    # Low margin
    low_margin = []
    if oz_f is not None and not oz_f.empty:
        for _, r in oz_f[oz_f["Маржинальность (%)"] < 15].iterrows():
            low_margin.append({"name": r["name"], "margin_pct": round(float(r["Маржинальность (%)"]), 1), "marketplace": "Ozon"})
    if wb_a is not None and not wb_a.empty:
        for _, r in wb_a[wb_a["margin"] < 15].iterrows():
            nm = r["Наименование"] if pd.notna(r.get("Наименование")) else r["sa_name"]
            low_margin.append({"name": nm, "margin_pct": round(float(r["margin"]), 1), "marketplace": "WB"})

    def _ym_s(d):
        return {"revenue": round(float(d["rev"]), 0), "profit": round(float(d["profit"]), 0),
                "margin_pct": round(float(d["margin"]), 1)}

    return {
        "period": {"day": YSTR, "month_start": month_start.strftime("%Y-%m-%d"), "month_end": yesterday.strftime("%Y-%m-%d")},
        "summary": {
            "yesterday": {
                "ozon": _s(oz_y), "wb": _s(wb_y), "ym": _ym_s(ym_y_ai),
                "total": {"revenue": round(t_y_rev, 0), "profit": round(t_y_prf, 0),
                           "margin_pct": round(t_y_prf / t_y_rev * 100, 1) if t_y_rev else 0}
            },
            "month": {
                "ozon": _s(oz_m, with_drr=True), "wb": _s(wb_m), "ym": _ym_s(ym_m_ai),
                "total": {"revenue": round(t_m_rev, 0), "profit": round(t_m_prf, 0),
                           "margin_pct": round(t_m_prf / t_m_rev * 100, 1) if t_m_rev else 0}
            },
        },
        "expenses_yesterday": {"ozon": _oz_expenses("oz_nach_y"), "wb": _wb_expenses("wb_df_y", "wb_sum_y")},
        "expenses_month": {"ozon": _oz_expenses("oz_nach_m"), "wb": _wb_expenses("wb_df_m", "wb_sum_m")},
        "top_sku_month": {"ozon": oz_top, "wb": wb_top},
        "low_margin_sku": low_margin,
    }

def _enrich_expenses_with_pct(exp, revenue):
    """Добавить pct_of_revenue в total и каждую группу, чтобы модель не считала сама."""
    if not exp or not revenue:
        return exp
    out = dict(exp)
    out["total_pct_of_revenue"] = round(out.get("total", 0) / revenue * 100, 1)
    if "groups" in out:
        new_groups = {}
        for k, g in out["groups"].items():
            ng = dict(g)
            ng["pct_of_revenue"] = round(g.get("total", 0) / revenue * 100, 1)
            new_groups[k] = ng
        out["groups"] = new_groups
    return out


def _build_comparison(y, m):
    """Сравнение вчера/месяц с явным направлением — чтобы модель не путала."""
    def _dir(yv, mv, tol=0.05):
        if abs(yv - mv) <= tol:
            return "equal"
        return "yesterday_higher" if yv > mv else "yesterday_lower"
    cmp = {}
    if "margin_pct" in y and "margin_pct" in m:
        cmp["margin_pct"] = {"yesterday": y["margin_pct"], "month": m["margin_pct"],
                             "direction": _dir(y["margin_pct"], m["margin_pct"])}
    if "drr_pct" in y and "drr_pct" in m:
        cmp["drr_pct"] = {"yesterday": y["drr_pct"], "month": m["drr_pct"],
                          "direction": _dir(y["drr_pct"], m["drr_pct"])}
    return cmp


def _build_mp_json(mp):
    """Build single-marketplace JSON for AI analysis (Ozon/WB pages)."""
    full = _build_full_json()
    key = "ozon" if mp == "Ozon" else "wb"

    sum_y = dict(full["summary"]["yesterday"][key])
    sum_m = dict(full["summary"]["month"][key])

    exp_y = full["expenses_yesterday"][key]
    exp_m = full["expenses_month"][key]
    rev_y = sum_y.get("revenue", 0)
    rev_m = sum_m.get("revenue", 0)

    # ДРР % за вчера: для Ozon — из «Оплата за клик» в расходах
    if mp == "Ozon" and rev_y:
        drr_y_total = exp_y.get("items", {}).get("Оплата за клик", 0)
        if drr_y_total:
            sum_y["drr_total"] = round(float(drr_y_total), 0)
            sum_y["drr_pct"] = round(drr_y_total / rev_y * 100, 1)

    exp_y_enriched = _enrich_expenses_with_pct(exp_y, rev_y)
    exp_m_enriched = _enrich_expenses_with_pct(exp_m, rev_m)

    return {
        "marketplace": "Ozon" if mp == "Ozon" else "Wildberries",
        "period": full["period"],
        "summary": {"yesterday": sum_y, "month": sum_m},
        "comparison": _build_comparison(sum_y, sum_m),
        "expenses": {"yesterday": exp_y_enriched, "month": exp_m_enriched},
        "top_sku": full["top_sku_month"][key],
        "low_margin_sku": [s for s in full["low_margin_sku"] if s["marketplace"] == ("Ozon" if mp == "Ozon" else "WB")],
    }

def _build_summary_text(full_json):
    """Build flat text summary for DeepSeek from full_json metrics."""
    s = full_json["summary"]
    oy = s["yesterday"]["ozon"]; wy = s["yesterday"]["wb"]; ty = s["yesterday"]["total"]
    ymy = s["yesterday"].get("ym", {"revenue": 0, "profit": 0, "margin_pct": 0})
    om = s["month"]["ozon"]; wm = s["month"]["wb"]; tm = s["month"]["total"]
    ymm = s["month"].get("ym", {"revenue": 0, "profit": 0, "margin_pct": 0})

    p = full_json["period"]
    lines = [
        f"СВОДКА МАРКЕТПЛЕЙСЫ",
        f"Период: {p['month_start']} — {p['month_end']}",
        "",
        "ВЧЕРА:",
        f"Ozon: выручка {_fm(oy['revenue'])} ₽, прибыль {_fm(oy['profit'])} ₽, маржинальность {oy['margin_pct']}%",
        f"WB: выручка {_fm(wy['revenue'])} ₽, прибыль {_fm(wy['profit'])} ₽, маржинальность {wy['margin_pct']}%",
        f"ЯМ: выручка {_fm(ymy['revenue'])} ₽, прибыль {_fm(ymy['profit'])} ₽, маржинальность {ymy['margin_pct']}%",
        f"Итого: выручка {_fm(ty['revenue'])} ₽, прибыль {_fm(ty['profit'])} ₽, маржинальность {ty['margin_pct']}%",
        "",
        "МЕСЯЦ (нарастающий итог):",
        f"Ozon: выручка {_fm(om['revenue'])} ₽, прибыль {_fm(om['profit'])} ₽, маржинальность {om['margin_pct']}%",
        f"WB: выручка {_fm(wm['revenue'])} ₽, прибыль {_fm(wm['profit'])} ₽, маржинальность {wm['margin_pct']}%",
        f"ЯМ: выручка {_fm(ymm['revenue'])} ₽, прибыль {_fm(ymm['profit'])} ₽, маржинальность {ymm['margin_pct']}%",
        f"Итого: выручка {_fm(tm['revenue'])} ₽, прибыль {_fm(tm['profit'])} ₽, маржинальность {tm['margin_pct']}%",
    ]

    # Expenses
    for period_label, exp_key in [("месяц", "expenses_month")]:
        for mp, mp_label in [("ozon", "OZON"), ("wb", "WB")]:
            exp = full_json[exp_key][mp]
            total = exp["total"]
            items = exp["items"]
            if items:
                lines.append("")
                lines.append(f"СТРУКТУРА РАСХОДОВ {mp_label} ({period_label}, всего {_fm(total)} ₽):")
                sorted_items = sorted(items.items(), key=lambda x: x[1], reverse=True)
                for name, val in sorted_items:
                    pct = f" ({val / total * 100:.0f}%)" if total and val / total > 0.1 else ""
                    lines.append(f"{name}: {_fm(val)}{pct}")

    # DRR
    drr_val = om.get("drr_total", 0)
    lines.append(f"\nДРР (реклама Ozon, месяц): {_fm(drr_val)} ₽" if drr_val else "\nДРР: нет данных")

    lines.append("\nЦЕЛЕВОЙ БЕНЧМАРК: маржинальность 30%")
    return "\n".join(lines)


_DEEPSEEK_SIGNAL_SYSTEM = """Ты — AI-аналитик платформы Алхимия. Анализируешь данные маркетплейсов и даёшь краткие однозначные оценки. Без воды, без вступлений.

Целевая маржинальность продавца: 30%. Ниже 20% — плохо. 20-30% — терпимо, нужно улучшать. Выше 30% — хорошо.

На основе переданных данных дай оценку в 4-6 предложений:
- Общая оценка: как дела относительно цели 30%
- Сравнение площадок: где лучше, где хуже, почему (смотри на структуру расходов)
- Главная проблема: что больше всего тянет маржу вниз
- Что делать: одна конкретная рекомендация

Пиши по-русски, кратко, прямо. Не используй списки и заголовки — просто связный текст."""


def _deepseek_call(system_prompt, user_prompt):
    """Call DeepSeek via OpenAI-compatible API, return answer or None."""
    creds = get_user_credentials(_USER_ID)
    DEEPSEEK_API_KEY = creds.get("DEEPSEEK_API_KEY", "")
    if not DEEPSEEK_API_KEY:
        return None
    import openai
    client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1200,
        temperature=0.3,
    )
    return resp.choices[0].message.content

def _giga_call(system_prompt, user_prompt):
    """Call GigaChat, return answer or None."""
    creds = get_user_credentials(_USER_ID)
    GIGACHAT_CREDENTIALS = creds.get("GIGACHAT_CREDENTIALS", "")
    if not GIGACHAT_CREDENTIALS:
        return None
    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole
    with GigaChat(credentials=GIGACHAT_CREDENTIALS, scope="GIGACHAT_API_PERS",
                  model="GigaChat", verify_ssl_certs=False) as giga:
        resp = giga.chat(Chat(messages=[
            Messages(role=MessagesRole.SYSTEM, content=system_prompt),
            Messages(role=MessagesRole.USER, content=user_prompt),
        ]))
        return resp.choices[0].message.content

_SIGNAL_SYSTEM = """Ты формируешь короткое AI-резюме для главной страницы дашборда e-commerce бизнеса на маркетплейсах.

Задача: дать мгновенно понятный статус и выделить только самые важные сигналы.

Правила:
- Не пересказывай цифры, пользователь их видит
- Не пиши подробный анализ
- Не хвали и не делай вводных фраз
- Если данных мало — скажи прямо

Верни ТОЛЬКО JSON без markdown и без пояснений:
{"status": "green | yellow | red", "title": "заголовок до 12 слов", "signals": ["сигнал до 12 слов", "максимум 3 штуки"]}

green = всё стабильно, критичных отклонений нет
yellow = есть точки внимания, требуется проверка
red = обнаружено заметное ухудшение, нужна срочная проверка"""

_ANALYSIS_SYSTEM = """Ты — AI-аналитик финансовых показателей e-commerce бизнеса на маркетплейсах.

Задача: проанализировать метрики маркетплейса и дать короткие полезные выводы для руководителя.

Правила:
- Не пересказывай все цифры — пользователь их видит в таблицах
- Разделяй анализ на два периода: с начала месяца и за вчера
- Группировка расходов уже готова в expenses.month.groups и expenses.yesterday.groups:
  • regulated (реклама, акции, скидки, подписки) — на них можно влиять
  • semi_regulated (логистика, хранение, возвраты, штрафы, утилизация) — влияние через ассортимент/поставки/качество
  • fixed (эквайринг, комиссия площадки) — принимаем как есть
- Используй эти группы как есть, не перегруппируй сам
- Не выдумывай причины. Если данных мало — пиши "вероятно" или "требует проверки"
- Указывай конкретные SKU и суммы где возможно
- Пиши коротко, без воды
- Не хвали и не добавляй вводные фразы ради объёма
- ЗАПРЕЩЕНО ссылаться на рыночные средние, отраслевые нормы, целевые бенчмарки и "обычно". Используй ТОЛЬКО цифры из JSON.
- ЗАПРЕЩЕНО считать проценты самому — все нужные pct_of_revenue, margin_pct, drr_pct и т.п. уже посчитаны в JSON. Бери готовые.
- Перед сравнением "выше/ниже" сверь оба числа: если A < B, пиши "ниже B", не "выше". Используй блок comparison с готовым полем direction.
- ЗАПРЕЩЕНО предлагать конкретные числовые цели, которых нет в JSON. Не пиши "снизить ДРР до 15%", "довести маржу до 30%" и т.п. Пиши просто "снизить", "пересмотреть", "проверить".

Формат:
- Каждый пункт с дефиса "- "
- Между пунктами одной секции — без пустых строк
- Между секциями — одна пустая строка

Структура ответа:

ВЧЕРА
- (2-3 пункта: выручка, маржа, аномалии если есть)

С НАЧАЛА МЕСЯЦА
- (2-3 пункта: динамика, тренд маржи, сравнение с планом если есть)

СТРУКТУРА РАСХОДОВ
- Регулируемые: (реклама, ДРР — сколько % от выручки, норма или много)
- Условно-регулируемые: (логистика, хранение — есть ли перекос)
- Нерегулируемые: (эквайринг, комиссия — просто констатация)

ПРОБЛЕМНЫЕ МЕСТА
- (2-4 пункта: конкретные SKU с низкой маржой, перерасход рекламы, затоваривание)

НА ЧТО ОБРАТИТЬ ВНИМАНИЕ
- (2-3 пункта: приоритетные действия на ближайшие дни)"""


def _is_section_header(ln):
    s = ln.strip()
    if not s:
        return False
    if s.startswith(("-", "•", "*")) or (len(s) > 1 and s[0].isdigit() and s[1] in ".)"):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return all(c.isupper() for c in letters)


def _normalize_ai_text(text):
    """Убрать пустые строки между пунктами; одна пустая строка перед заголовком секции."""
    if not text:
        return text
    raw = [ln.rstrip() for ln in str(text).splitlines()]
    non_empty = [ln for ln in raw if ln.strip()]
    if not non_empty:
        return ""
    out = []
    for i, ln in enumerate(non_empty):
        if i > 0 and _is_section_header(ln):
            out.append("")
        out.append(ln)
    return "\n".join(out).strip()


def _ai_text_to_html(text):
    """Готовый HTML без markdown-парсинга: каждая строка отдельным <div>,
    заголовки секций жирные с отступом, пустых параграфов не возникает."""
    import html as _html
    norm = _normalize_ai_text(text)
    if not norm:
        return ""
    parts = []
    for ln in norm.split("\n"):
        s = ln.strip()
        if not s:
            parts.append('<div style="height:6px"></div>')
            continue
        if _is_section_header(ln):
            parts.append(
                f'<div style="font-weight:700;margin-top:8px;color:#0f172a">'
                f'{_html.escape(s)}</div>'
            )
        else:
            parts.append(f'<div>{_html.escape(s)}</div>')
    return "".join(parts)

def _show_signal_card(answer):
    """Parse signal JSON and show colored card."""
    try:
        # strip markdown fences if any
        txt = answer.strip()
        if txt.startswith("```"):
            txt = txt.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = _json.loads(txt)
        status = data.get("status", "yellow")
        title = data.get("title", "")
        signals = data.get("signals", [])
    except Exception:
        # Can't parse — show as text
        st.markdown(
            f'<div style="border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
            f'<div style="font-size:13px;font-weight:700;color:#1e293b;margin-bottom:10px">🤖 AI-сигнал</div>'
            f'<div style="font-size:14px;color:#1e293b;line-height:1.7;white-space:pre-wrap">{answer}</div>'
            f'</div>', unsafe_allow_html=True)
        return

    colors = {
        "green":  {"border": "#86efac", "bg": "linear-gradient(135deg,#f0fdf4,#ecfdf5)", "icon": "✅", "text": "#166534"},
        "yellow": {"border": "#fde68a", "bg": "linear-gradient(135deg,#fefce8,#fffbeb)", "icon": "⚠️", "text": "#854d0e"},
        "red":    {"border": "#fca5a5", "bg": "linear-gradient(135deg,#fef2f2,#fff1f2)", "icon": "🔴", "text": "#991b1b"},
    }
    c = colors.get(status, colors["yellow"])
    signals_html = "".join(f'<div style="font-size:13px;color:#374151;margin-top:4px">• {s}</div>' for s in signals[:3])
    st.markdown(
        f'<div style="border:2px solid {c["border"]};border-radius:14px;padding:20px 24px;'
        f'background:{c["bg"]};box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
        f'<div style="font-size:18px;font-weight:700;color:{c["text"]};margin-bottom:8px">{c["icon"]} {title}</div>'
        f'{signals_html}'
        f'</div>', unsafe_allow_html=True)

def _show_auto_fallback(full_json):
    """Show auto-analysis fallback from data."""
    fb = []
    m_y = full_json["summary"]["yesterday"]["total"]["margin_pct"]
    m_m = full_json["summary"]["month"]["total"]["margin_pct"]
    fb.append(f"**Маржинальность вчера: {m_y:.1f}%** {'✅' if m_y >= 30 else '⚠️'}")
    fb.append(f"**Маржинальность месяц: {m_m:.1f}%** {'✅' if m_m >= 30 else '⚠️'}")
    # Top expenses
    for period_label, exp_key in [("месяц", "expenses_month")]:
        for mp in ["ozon", "wb"]:
            items = full_json[exp_key][mp]["items"]
            if items:
                top3 = sorted(items.items(), key=lambda x: x[1], reverse=True)[:3]
                mp_label = "Ozon" if mp == "ozon" else "WB"
                fb.append(f"\n**Топ расходов {mp_label} ({period_label}):**")
                for name, val in top3:
                    fb.append(f"- {name}: {_fm(val)} ₽")
    # Low margin
    low = full_json["low_margin_sku"]
    if low:
        fb.append(f"\n**SKU с маржой ниже 15% ({len(low)} шт):**")
        for s in sorted(low, key=lambda x: x["margin_pct"])[:10]:
            fb.append(f"- {s['name']} ({s['marketplace']}): {s['margin_pct']:.1f}%")
        if len(low) > 10:
            fb.append(f"  ...и ещё {len(low) - 10}")
    else:
        fb.append("\n**SKU с маржой ниже 15%:** нет ✅")
    return fb

def _show_mp_auto_fallback(mp_json, brand_color):
    """Show per-marketplace auto fallback."""
    fb = []
    m_m = mp_json["summary"]["month"]["margin_pct"]
    fb.append(f"**Маржинальность месяц: {m_m:.1f}%** {'✅' if m_m >= 30 else '⚠️'}")
    # Expenses
    items = mp_json["expenses"]["month"]["items"]
    if items:
        top3 = sorted(items.items(), key=lambda x: x[1], reverse=True)[:3]
        fb.append("\n**Ключевые расходы:**")
        for name, val in top3:
            fb.append(f"- {name}: {_fm(val)} ₽")
    # Top SKU
    if mp_json["top_sku"]:
        fb.append("\n**Топ-3 SKU по выручке:**")
        for s in mp_json["top_sku"][:3]:
            drr_str = f", ДРР {_fm(s['drr'])} ₽" if "drr" in s else ""
            fb.append(f"- {s['name']}: маржа {s['margin_pct']:.1f}%{drr_str}")
    # Low margin
    low = mp_json["low_margin_sku"]
    if low:
        fb.append(f"\n**SKU с маржой ниже 15% ({len(low)} шт):**")
        for s in sorted(low, key=lambda x: x["margin_pct"])[:10]:
            fb.append(f"- {s['name']}: {s['margin_pct']:.1f}%")
        if len(low) > 10:
            fb.append(f"  ...и ещё {len(low) - 10}")
    else:
        fb.append("\n**SKU с маржой ниже 15%:** нет ✅")
    border = "#bfdbfe" if brand_color == "ozon" else "#ddd6fe"
    bg = "linear-gradient(135deg,#eff6ff,#f0f9ff)" if brand_color == "ozon" else "linear-gradient(135deg,#f5f3ff,#faf5ff)"
    tc = "#2563eb" if brand_color == "ozon" else "#7c3aed"
    st.markdown(
        f'<div style="border:1px solid {border};border-radius:14px;padding:20px 24px;'
        f'background:{bg};box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
        f'<div style="font-size:13px;font-weight:700;color:{tc};margin-bottom:10px">📊 Автоматический анализ</div>'
        f'<div style="font-size:14px;color:#1e293b;line-height:1.7">'
        + "<br>".join(fb).replace("\n", "<br>")
        + '</div></div>', unsafe_allow_html=True)


CARD_TITLES = {
    "Ozon": ("OZON", "#2563eb"),
    "WB":   ("WILDBERRIES", "#7c3aed"),
    "ЯМ":   ("ЯНДЕКС МАРКЕТ", "#ca8a04"),
    "Итого": ("ИТОГО", "#1e293b"),
}
CARD_NAVS = {"Ozon": "🔵 Ozon", "WB": "🟣 WB", "ЯМ": "🟡 Яндекс Маркет"}

def _card(title, rev, profit, margin, btn_key=None):
    """Clickable card with hover translateY effect."""
    name, name_color = CARD_TITLES.get(title, (title, "#1e293b"))
    m_color = "#16a34a" if margin >= 30 else "#ca8a04" if margin >= 15 else "#dc2626"
    p_color = "#16a34a" if profit >= 0 else "#dc2626"
    clickable = title in CARD_NAVS

    card_inner = (
        f'<div style="font-size:15px;font-weight:800;color:{name_color};margin-bottom:12px;letter-spacing:0.5px">'
        f'{name}</div>'
        f'<div style="display:flex;align-items:center;gap:16px">'
        f'<div style="flex:1">'
        f'<div style="font-size:11px;color:#9ca3af;margin-bottom:2px">Выручка</div>'
        f'<div style="font-size:15px;font-weight:600;color:#1e293b;margin-bottom:8px;'
        f'font-feature-settings:\'tnum\'">{_fm(rev)} ₽</div>'
        f'<div style="font-size:11px;color:#9ca3af;margin-bottom:2px">Прибыль</div>'
        f'<div style="font-size:15px;font-weight:600;color:{p_color};'
        f'font-feature-settings:\'tnum\'">{_fm(profit)} ₽</div>'
        f'</div>'
        f'<div style="text-align:right">'
        f'<div style="font-size:11px;color:#9ca3af;margin-bottom:2px">Маржинальность</div>'
        f'<div style="font-size:44px;font-weight:800;color:{m_color};line-height:1.1;'
        f'font-feature-settings:\'tnum\'">'
        f'{margin:.1f}%</div>'
        f'</div>'
        f'</div>'
    )

    if clickable:
        st.markdown(
            f'<style>'
            f'.alch-card-link,.alch-card-link:hover,.alch-card-link:visited,.alch-card-link:active'
            f'{{text-decoration:none!important;display:block;color:inherit}}'
            f'.alch-card{{border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04),0 1px 2px rgba(0,0,0,0.02);background:#fff;'
            f'transition:all 0.3s cubic-bezier(0.4,0,0.2,1);cursor:pointer}}'
            f'.alch-card:hover{{transform:translateY(-2px);box-shadow:0 8px 25px rgba(0,0,0,0.08)}}'
            f'</style>'
            f'<a href="?nav={title}" target="_self" class="alch-card-link">'
            f'<div class="alch-card">{card_inner}</div></a>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<style>'
            f'.alch-card-static{{border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04),0 1px 2px rgba(0,0,0,0.02);background:#fff}}'
            f'</style>'
            f'<div class="alch-card-static">{card_inner}</div>',
            unsafe_allow_html=True,
        )

def _summary_row(label, oz, wb, suffix=""):
    """Render one row of cards: Ozon | WB | Итого."""
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

_NAV_MAP = {"Ozon": "🔵 Ozon", "WB": "🟣 WB", "YM": "🟡 Яндекс Маркет"}

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
    st.title("Alchemy ⚗️")

    # User info
    st.caption(st.session_state.get("user_name", ""))
    if st.session_state.get("user_company"):
        st.caption(st.session_state["user_company"])

    st.divider()

    pages = [
        "⚗️ Сводка",
        "🔵 Ozon",
        "🟣 WB",
        "🟡 Яндекс Маркет",
        "🚛 Поставки",
    ]
    settings_pages = ["🔑 API-ключи", "📋 Прайс-лист"]
    admin_pages = ["👥 Пользователи"] if st.session_state.get("is_admin") else []
    all_pages = pages + settings_pages + admin_pages

    nav_idx = 0
    if "nav" in st.session_state and st.session_state["nav"] in all_pages:
        nav_idx = all_pages.index(st.session_state["nav"])
        del st.session_state["nav"]
    page = st.radio("Навигация", all_pages, index=nav_idx, label_visibility="collapsed")

    st.divider()
    age = cache_age_minutes(_USER_ID)
    ts = cache_timestamp(_USER_ID)
    if age is not None:
        st.caption(f"Обновлено: {ts}")
    else:
        st.caption("⚠️ Кэш отсутствует")

    btn_label = "Загрузить данные" if age is None else "Обновить данные"
    if st.button(btn_label, use_container_width=True,
                 type="primary" if age is None or age >= 60 else "secondary"):
        from data_loader import refresh_all_data
        creds = get_user_credentials(_USER_ID)
        price_path = get_user_price_path(_USER_ID)
        with st.spinner("Загрузка данных с API..."):
            refresh_all_data(user_id=_USER_ID, creds=creds,
                           price_path=str(price_path) if price_path else None)
        st.cache_data.clear()
        st.rerun()

    st.divider()
    if st.button("Выйти", use_container_width=True):
        logout()
        st.rerun()


# ── Settings/Admin pages (no cache needed) ───────────────────────────────

if page == "🔑 API-ключи":
    from pages.settings_api import render as _render_api
    _render_api(_USER_ID)
    st.stop()

if page == "📋 Прайс-лист":
    from pages.settings_price import render as _render_price
    _render_price(_USER_ID)
    st.stop()

if page == "👥 Пользователи":
    from pages.admin_users import render as _render_admin
    _render_admin()
    st.stop()

# ── gate ─────────────────────────────────────────────────────────────────

if cache_age_minutes(_USER_ID) is None:
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

    # ── AI ──
    if st.button("ИИ анализ ситуации", key="main_ai_signal", type="primary"):
        full_json = _build_full_json()
        summary_text = _build_summary_text(full_json)
        margin_month = full_json["summary"]["month"]["total"]["margin_pct"]

        # Цветной индикатор — определяется Python
        if margin_month >= 30:
            _sig_status, _sig_icon = "green", "✅"
            _sig_border, _sig_bg, _sig_text_c = "#86efac", "linear-gradient(135deg,#f0fdf4,#ecfdf5)", "#166534"
        elif margin_month >= 20:
            _sig_status, _sig_icon = "yellow", "⚠️"
            _sig_border, _sig_bg, _sig_text_c = "#fde68a", "linear-gradient(135deg,#fefce8,#fffbeb)", "#854d0e"
        else:
            _sig_status, _sig_icon = "red", "🔴"
            _sig_border, _sig_bg, _sig_text_c = "#fca5a5", "linear-gradient(135deg,#fef2f2,#fff1f2)", "#991b1b"

        answer = None
        with st.spinner("Анализирую..."):
            # DeepSeek (приоритет) → GigaChat (фоллбэк)
            try:
                answer = _deepseek_call(_DEEPSEEK_SIGNAL_SYSTEM, summary_text)
            except Exception:
                pass
            if not answer:
                try:
                    answer = _giga_call(_SIGNAL_SYSTEM, _json.dumps(full_json, ensure_ascii=False))
                except Exception:
                    pass

        if answer:
            # DeepSeek возвращает текст → показываем в цветной карточке
            # Пробуем распарсить как JSON (если GigaChat ответил)
            _parsed_json = False
            try:
                txt = answer.strip()
                if txt.startswith("```"):
                    txt = txt.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                data = _json.loads(txt)
                # GigaChat JSON format — use _show_signal_card
                _show_signal_card(answer)
                _parsed_json = True
            except Exception:
                pass

            if not _parsed_json:
                # DeepSeek text format — show in colored card
                st.markdown(
                    f'<div style="border:2px solid {_sig_border};border-radius:14px;padding:20px 24px;'
                    f'background:{_sig_bg};box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
                    f'<div style="font-size:18px;font-weight:700;color:{_sig_text_c};margin-bottom:8px">'
                    f'{_sig_icon} Маржинальность {margin_month:.1f}%</div>'
                    f'<div style="font-size:14px;color:#1e293b;line-height:1.7;white-space:pre-wrap">'
                    f'{_normalize_ai_text(answer)}</div></div>', unsafe_allow_html=True)
        else:
            st.warning("AI временно недоступен")
            fb = _show_auto_fallback(full_json)
            st.markdown(
                f'<div style="border:2px solid {_sig_border};border-radius:14px;padding:20px 24px;'
                f'background:{_sig_bg};'
                f'box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
                f'<div style="font-size:13px;font-weight:700;color:{_sig_text_c};margin-bottom:10px">'
                f'{_sig_icon} Автоматический анализ (маржа {margin_month:.1f}%)</div>'
                f'<div style="font-size:14px;color:#1e293b;line-height:1.7">'
                + "<br>".join(fb).replace("\n", "<br>")
                + '</div></div>', unsafe_allow_html=True)

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
        "#2563eb", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6",
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
            domain=dict(x=[0.15, 0.85], y=[0.1, 0.9]),
        ))
        fig.update_layout(
            height=380,
            showlegend=False,
            margin=dict(l=60, r=60, t=40, b=40),
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
            marker_color="#2563eb",
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
            marker_color="#7c3aed",
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

    # ── 3 cards: marketplace | yesterday | month ──
    def _info_card(html):
        st.markdown(
            f'<div style="border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04);background:#fff;height:100%">{html}</div>',
            unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _info_card(
            f'<div style="font-size:28px;font-weight:800;color:#2563eb;margin-bottom:12px;letter-spacing:1px">OZON</div>'
            f'<div style="font-size:12px;color:#9ca3af">SKU с продажами (месяц)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#1e293b">{om["sku"]}</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:6px">{MSTR}</div>'
        )
    with c2:
        ym_color = "#16a34a" if oy["margin"] >= 30 else "#ca8a04" if oy["margin"] >= 15 else "#dc2626"
        yp_color = "#16a34a" if oy["profit"] >= 0 else "#dc2626"
        _info_card(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">ВЧЕРА</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">Отгрузка</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(oy["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{yp_color};font-feature-settings:\'tnum\'">{_fm(oy["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{ym_color};line-height:1.1;font-feature-settings:\'tnum\'">{oy["margin"]:.1f}%</div>'
            f'</div></div>'
        )
    with c3:
        mm_color = "#16a34a" if om["margin"] >= 30 else "#ca8a04" if om["margin"] >= 15 else "#dc2626"
        mp_color = "#16a34a" if om["profit"] >= 0 else "#dc2626"
        _info_card(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">МЕСЯЦ</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">Отгрузка</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(om["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{mp_color};font-feature-settings:\'tnum\'">{_fm(om["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{mm_color};line-height:1.1;font-feature-settings:\'tnum\'">{om["margin"]:.1f}%</div>'
            f'</div></div>'
        )

    # ── Period radio ──
    oz_period = st.radio("Период", ["Вчера", "Месяц"], horizontal=True, key="oz_period")
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
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#2563eb"></span>ЮНИТ-ЭКОНОМИКА ПО SKU</div>', unsafe_allow_html=True)

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
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#2563eb"></span>АНАЛИЗ ДРР</div>', unsafe_allow_html=True)

    oz_drr_period = st.radio("Период", ["Вчера", "Месяц"], horizontal=True, key="oz_drr_period")
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
                marker_color="#2563eb",
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

    # ── AI-анализ ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#2563eb"></span>AI-АНАЛИЗ</div>', unsafe_allow_html=True)

    if st.button("ИИ анализ ситуации", key="oz_ai_btn", type="primary"):
        mp_json = _build_mp_json("Ozon")
        answer = None
        with st.spinner("Анализирую..."):
            try:
                answer = _deepseek_call(_ANALYSIS_SYSTEM, _json.dumps(mp_json, ensure_ascii=False))
            except Exception:
                pass
            if not answer:
                try:
                    answer = _giga_call(_ANALYSIS_SYSTEM, _json.dumps(mp_json, ensure_ascii=False))
                except Exception:
                    pass
        if answer:
            st.markdown(
                f'<div style="border:1px solid #bfdbfe;border-radius:14px;padding:20px 24px;'
                f'background:linear-gradient(135deg,#eff6ff,#f0f9ff);'
                f'box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
                f'<div style="font-size:13px;font-weight:700;color:#2563eb;margin-bottom:10px">🤖 AI-анализ</div>'
                f'<div style="font-size:14px;color:#1e293b;line-height:1.6">{_ai_text_to_html(answer)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.warning("AI временно недоступен")
            _show_mp_auto_fallback(mp_json, "ozon")


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

    # ── 3 cards: marketplace | yesterday | month ──
    def _info_card_wb(html):
        st.markdown(
            f'<div style="border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04);background:#fff;height:100%">{html}</div>',
            unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _info_card_wb(
            f'<div style="font-size:28px;font-weight:800;color:#7c3aed;margin-bottom:12px;letter-spacing:1px">WILDBERRIES</div>'
            f'<div style="font-size:12px;color:#9ca3af">SKU с продажами (месяц)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#1e293b">{wm["sku"]}</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:6px">{MSTR}</div>'
        )
    with c2:
        ym_color = "#16a34a" if wy["margin"] >= 30 else "#ca8a04" if wy["margin"] >= 15 else "#dc2626"
        yp_color = "#16a34a" if wy["profit"] >= 0 else "#dc2626"
        _info_card_wb(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">ВЧЕРА</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">К выплате</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(wy["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{yp_color};font-feature-settings:\'tnum\'">{_fm(wy["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{ym_color};line-height:1.1;font-feature-settings:\'tnum\'">{wy["margin"]:.1f}%</div>'
            f'</div></div>'
        )
    with c3:
        mm_color = "#16a34a" if wm["margin"] >= 30 else "#ca8a04" if wm["margin"] >= 15 else "#dc2626"
        mp_color = "#16a34a" if wm["profit"] >= 0 else "#dc2626"
        _info_card_wb(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">МЕСЯЦ</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">К выплате</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(wm["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{mp_color};font-feature-settings:\'tnum\'">{_fm(wm["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{mm_color};line-height:1.1;font-feature-settings:\'tnum\'">{wm["margin"]:.1f}%</div>'
            f'</div></div>'
        )

    # ── Period radio ──
    wb_period = st.radio("Период", ["Вчера", "Месяц"], horizontal=True, key="wb_period")
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
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>ЮНИТ-ЭКОНОМИКА ПО SKU</div>', unsafe_allow_html=True)

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
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>АНАЛИЗ ДРР</div>', unsafe_allow_html=True)

    wb_drr_period = st.radio("Период", ["Вчера", "Месяц"], horizontal=True, key="wb_drr_period")
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
                marker_color="#7c3aed",
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

    # ── AI-анализ ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>AI-АНАЛИЗ</div>', unsafe_allow_html=True)

    if st.button("ИИ анализ ситуации", key="wb_ai_btn", type="primary"):
        mp_json = _build_mp_json("WB")
        answer = None
        with st.spinner("Анализирую..."):
            try:
                answer = _deepseek_call(_ANALYSIS_SYSTEM, _json.dumps(mp_json, ensure_ascii=False))
            except Exception:
                pass
            if not answer:
                try:
                    answer = _giga_call(_ANALYSIS_SYSTEM, _json.dumps(mp_json, ensure_ascii=False))
                except Exception:
                    pass
        if answer:
            st.markdown(
                f'<div style="border:1px solid #ddd6fe;border-radius:14px;padding:20px 24px;'
                f'background:linear-gradient(135deg,#f5f3ff,#faf5ff);'
                f'box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:12px">'
                f'<div style="font-size:13px;font-weight:700;color:#7c3aed;margin-bottom:10px">🤖 AI-анализ</div>'
                f'<div style="font-size:14px;color:#1e293b;line-height:1.6">{_ai_text_to_html(answer)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.warning("AI временно недоступен")
            _show_mp_auto_fallback(mp_json, "wb")


# ══════════════════════════════════════════════════════════════════════════
# 🟡 Яндекс Маркет
# ══════════════════════════════════════════════════════════════════════════

elif page == "🟡 Яндекс Маркет":
    ym_y = C("ym_margin_y")
    ym_m = C("ym_margin_m")
    if ym_m is None:
        st.warning("Нет данных ЯМ. Обновите кэш.")
        st.stop()

    from ym_margin import SVC_ORDER as YM_SVC_ORDER, SVC_MAIN as YM_SVC_MAIN

    def _ym_summary(d):
        """Extract summary metrics from cached YM margin data."""
        if d is None:
            return {"rev": 0, "profit": 0, "margin": 0, "sku": 0}
        R = d["R"]
        totals = d["totals"]
        if R is None or R.empty:
            return {"rev": 0, "profit": 0, "margin": 0, "sku": 0}
        rev = R["revenue"].sum()
        costs = sum(totals.values())
        sebes = R["sebes_total"].sum()
        profit = rev - costs - sebes
        margin = (profit / rev * 100) if rev else 0
        return {"rev": rev, "profit": profit, "margin": margin,
                "sku": len(R[R["qty"] > 0]), "costs": costs, "sebes": sebes}

    ymy = _ym_summary(ym_y)
    ymm = _ym_summary(ym_m)

    # ── 3 cards ──
    def _info_card_ym(html):
        st.markdown(
            f'<div style="border:1px solid #e5e7eb;border-radius:14px;padding:20px 24px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04);background:#fff;height:100%">{html}</div>',
            unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _info_card_ym(
            f'<div style="font-size:28px;font-weight:800;color:#ca8a04;margin-bottom:12px;letter-spacing:1px">ЯНДЕКС МАРКЕТ</div>'
            f'<div style="font-size:12px;color:#9ca3af">SKU с продажами (месяц)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#1e293b">{ymm["sku"]}</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:6px">{MSTR}</div>'
        )
    with c2:
        ym_yc = "#16a34a" if ymy["margin"] >= 30 else "#ca8a04" if ymy["margin"] >= 15 else "#dc2626"
        yp_c = "#16a34a" if ymy["profit"] >= 0 else "#dc2626"
        _info_card_ym(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">ВЧЕРА</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">Выручка</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(ymy["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{yp_c};font-feature-settings:\'tnum\'">{_fm(ymy["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{ym_yc};line-height:1.1;font-feature-settings:\'tnum\'">{ymy["margin"]:.1f}%</div>'
            f'</div></div>'
        )
    with c3:
        ym_mc = "#16a34a" if ymm["margin"] >= 30 else "#ca8a04" if ymm["margin"] >= 15 else "#dc2626"
        mp_c = "#16a34a" if ymm["profit"] >= 0 else "#dc2626"
        _info_card_ym(
            f'<div style="font-size:12px;font-weight:700;color:#9ca3af;letter-spacing:1px;margin-bottom:8px">МЕСЯЦ</div>'
            f'<div style="display:flex;align-items:center;gap:16px">'
            f'<div style="flex:1">'
            f'<div style="font-size:11px;color:#9ca3af">Выручка</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;font-feature-settings:\'tnum\'">{_fm(ymm["rev"])} ₽</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">Прибыль</div>'
            f'<div style="font-size:15px;font-weight:600;color:{mp_c};font-feature-settings:\'tnum\'">{_fm(ymm["profit"])} ₽</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:11px;color:#9ca3af">Маржа</div>'
            f'<div style="font-size:40px;font-weight:800;color:{ym_mc};line-height:1.1;font-feature-settings:\'tnum\'">{ymm["margin"]:.1f}%</div>'
            f'</div></div>'
        )

    st.divider()

    # ── Период toggle ──
    ym_period = st.radio("Период", ["Вчера", "Месяц"], horizontal=True, key="ym_period")
    ym_data = ym_y if ym_period == "Вчера" else ym_m
    if ym_data is None:
        st.info("Нет данных за выбранный период")
        st.stop()

    ym_R = ym_data["R"]
    ym_totals = ym_data["totals"]

    # ── Структура расходов (pie chart) ──
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#ca8a04"></span>СТРУКТУРА РАСХОДОВ (ГРОСС)</div>', unsafe_allow_html=True)

    svc_items = [(k, v) for k, v in ym_totals.items() if v > 0]
    if svc_items:
        col_pie, col_tbl = st.columns([1, 1])
        with col_pie:
            svc_df = pd.DataFrame(svc_items, columns=["Услуга", "Сумма"])
            fig_pie = px.pie(
                svc_df, values="Сумма", names="Услуга",
                color_discrete_sequence=["#ca8a04", "#f59e0b", "#fbbf24", "#fcd34d",
                                         "#fde68a", "#fef3c7", "#d97706", "#b45309"],
                hole=0.45,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label",
                                  textfont_size=11)
            fig_pie.update_layout(
                height=350, margin=dict(t=10, b=10, l=10, r=10),
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_pie, use_container_width=True, key="ym_pie",
                            config={"displayModeBar": False})
        with col_tbl:
            gross_svc = sum(ym_totals.values())
            lines = []
            for svc in YM_SVC_ORDER:
                val = ym_totals.get(svc, 0)
                if val > 0:
                    pct = val / gross_svc * 100 if gross_svc else 0
                    lines.append(f"  {svc}: **{_fm(val)}** ₽ ({pct:.0f}%)")
            lines.append(f"  **ИТОГО: {_fm(gross_svc)} ₽**")
            st.markdown("\n".join(lines))

    # ── SKU таблица ──
    st.divider()
    st.markdown('<div class="section-label"><span class="section-dot" style="background:#ca8a04"></span>ЮНИТ-ЭКОНОМИКА ПО SKU</div>', unsafe_allow_html=True)

    if ym_R is not None and not ym_R.empty:
        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            ym_q = st.text_input("Поиск SKU / название", key="ym_q",
                                  placeholder="Артикул или название...",
                                  label_visibility="collapsed")
        with fc2:
            ym_mr_from = st.number_input("Маржа от %", value=-100, step=1, key="ym_mr_from")
        with fc3:
            ym_mr_to = st.number_input("Маржа до %", value=80, step=1, key="ym_mr_to")

        # Build display table
        tbl = ym_R.copy()
        # Колонки уже правильные из calc_margin:
        # sku, name, qty, revenue, Размещение..Перевод, Прочее, Затраты,
        # sebes_unit, sebes_total, Прибыль, Маржа_pct

        display_cols = ["sku", "name", "qty", "revenue"]
        display_cols += [s for s in YM_SVC_MAIN if s in tbl.columns]
        display_cols += ["Прочее", "Затраты", "sebes_unit", "sebes_total", "Прибыль", "Маржа_pct"]

        rename = {
            "sku": "Артикул", "name": "Название", "qty": "Шт",
            "revenue": "Выручка", "Затраты": "Затраты итого",
            "sebes_unit": "Себест/шт", "sebes_total": "Себестоимость",
            "Маржа_pct": "Маржа %",
        }

        tbl = tbl[[c for c in display_cols if c in tbl.columns]].copy()
        tbl = tbl.rename(columns=rename)

        # Apply filters
        if ym_q:
            tbl = tbl[tbl["Артикул"].astype(str).str.contains(ym_q, case=False, na=False) |
                       tbl["Название"].astype(str).str.contains(ym_q, case=False, na=False)]
        tbl = tbl[(tbl["Маржа %"] >= ym_mr_from) & (tbl["Маржа %"] <= ym_mr_to)]

        fmt = {"Выручка": "{:,.0f}", "Себестоимость": "{:,.0f}", "Затраты итого": "{:,.0f}",
               "Прибыль": "{:,.0f}", "Маржа %": "{:.1f}%",
               "Себест/шт": "{:,.0f}", "Прочее": "{:,.0f}"}
        for s in YM_SVC_MAIN:
            fmt[s] = "{:,.0f}"

        styled = (tbl.style.format(fmt, na_rep="—")
                  .map(_margin_bar, subset=["Маржа %"]))
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
        st.caption(f"{len(tbl)} SKU")

        # ── Топ-15 по выручке ──
        st.divider()
        st.markdown('<div class="section-label"><span class="section-dot" style="background:#ca8a04"></span>ТОП-15 SKU ПО ВЫРУЧКЕ</div>', unsafe_allow_html=True)

        top15 = ym_R[ym_R["qty"] > 0].sort_values("revenue", ascending=False).head(15)
        top15 = top15.sort_values("revenue", ascending=True)
        top15["short"] = top15["name"].str[:40]

        fig_top = go.Figure(go.Bar(
            y=top15["short"], x=top15["revenue"],
            orientation="h",
            marker_color="#ca8a04",
            customdata=list(zip(
                top15["name"], top15["revenue"], top15["Прибыль"], top15["Маржа_pct"],
            )),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Выручка: %{customdata[1]:,.0f} ₽<br>"
                "Прибыль: %{customdata[2]:,.0f} ₽<br>"
                "Маржа: %{customdata[3]:.1f}%"
                "<extra></extra>"
            ),
        ))
        fig_top.update_layout(
            height=520,
            margin=dict(l=10, r=10, t=10, b=10),
            bargap=0.3,
            showlegend=False,
            xaxis_title="Выручка, ₽",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        with st.container(border=True):
            st.plotly_chart(fig_top, use_container_width=True, key="ym_top15",
                            config={"displayModeBar": False})

        # ── Excel export ──
        st.divider()
        import io as _io_ym_margin
        ym_buf = _io_ym_margin.BytesIO()
        export_cols = ["sku", "name", "qty", "revenue"]
        export_cols += [s for s in YM_SVC_MAIN if s in ym_R.columns]
        export_cols += ["Прочее", "Затраты", "sebes_unit", "sebes_total", "Прибыль", "Маржа_pct"]
        export_df = ym_R[[c for c in export_cols if c in ym_R.columns]].copy()
        exp_rename = {
            "sku": "Артикул", "name": "Название", "qty": "Шт",
            "revenue": "Выручка", "Затраты": "Затраты итого",
            "sebes_unit": "Себест/шт", "sebes_total": "Себестоимость",
            "Маржа_pct": "Маржа %",
        }
        export_df = export_df.rename(columns=exp_rename)
        export_df.to_excel(ym_buf, index=False, sheet_name="ЯМ Маржа")
        st.download_button(
            "Скачать Excel", data=ym_buf.getvalue(),
            file_name=f"ym_margin_{ym_period.lower()}_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ym_margin_download",
        )
    else:
        st.info("Нет данных по SKU")


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

    # ── Загрузка квантов ──
    with st.expander("Загрузить файл квантов"):
        st.caption("Excel с колонками: Артикул, квант, Цена, Название. Текущий файл: quantum_stock.xlsx")
        _q_file = st.file_uploader("Файл квантов", type=["xlsx", "xls"], key="quantum_upload",
                                    label_visibility="collapsed")
        if _q_file:
            import shutil, io as _io_q
            _q_bytes = _q_file.getvalue()
            try:
                _q_df = pd.read_excel(_io_q.BytesIO(_q_bytes))
                if "Артикул" not in _q_df.columns or "квант" not in _q_df.columns:
                    st.error(f"Нужны колонки «Артикул» и «квант». В файле: {', '.join(_q_df.columns.tolist())}")
                else:
                    st.success(f"Найдено {len(_q_df)} позиций")
                    st.dataframe(_q_df.head(10), use_container_width=True, hide_index=True)
                    if st.button("Сохранить кванты", type="primary", key="save_quants"):
                        with open("quantum_stock.xlsx", "wb") as f:
                            f.write(_q_bytes)
                        st.success("Файл квантов обновлён")
                        st.rerun()
            except Exception as e:
                st.error(f"Ошибка чтения файла: {e}")

    mp_choice = st.radio("Маркетплейс", ["Ozon", "WB", "Yandex Market"], horizontal=True, key="supply_mp")

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
                _qf = f"data/user_{_USER_ID}/quantum_stock.xlsx"
                wb_result = compute_wb_supply_data(days_plan=wb_depth, creds=get_user_credentials(_USER_ID), quantum_file=_qf, user_id=_USER_ID)
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
            '<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>'
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

    # ── Yandex Market ──
    if mp_choice == "Yandex Market":
        st.caption("Яндекс Маркет FBY/FBS")

        ym_lk_file = st.file_uploader(
            "Файл «Остатки по кластерам» из ЛК ЯМ (необязательно)",
            type=["xlsx", "xls"],
            key="ym_supply_lk_file",
            help="Скачайте из ЛК ЯМ → Аналитика → Остатки по кластерам. "
                 "Если не загружен — данные возьмутся из API (менее точно).",
        )

        ymc1, ymc2 = st.columns([1, 3])
        with ymc1:
            ym_depth = st.number_input("Глубина поставки, дней", min_value=14, max_value=180,
                                       value=60, step=7, key="ym_supply_depth")
        with ymc2:
            st.write("")
            st.write("")
            ym_calc_btn = st.button("Рассчитать", type="primary", key="ym_supply_calc")

        if ym_calc_btn:
            lk_bytes = ym_lk_file.getvalue() if ym_lk_file else None
            spinner_msg = ("Парсинг файла из ЛК и расчёт..." if lk_bytes
                           else "Загрузка данных из API Яндекс Маркет и расчёт...")
            with st.spinner(spinner_msg):
                from ym_supply import compute_ym_supply_data
                _qf = f"data/user_{_USER_ID}/quantum_stock.xlsx"
                ym_result = compute_ym_supply_data(days_plan=ym_depth, lk_file_bytes=lk_bytes, creds=get_user_credentials(_USER_ID), quantum_file=_qf)
                st.session_state["ym_supply_result"] = ym_result
                st.session_state["ym_supply_depth_used"] = ym_depth

        ym_result = st.session_state.get("ym_supply_result")
        if ym_result is None:
            st.info("Нажмите «Рассчитать» для загрузки данных")
            st.stop()

        ym_depth_used = st.session_state.get("ym_supply_depth_used", 60)
        ym_cp = ym_result["cluster_priority"]
        ym_plan = ym_result["plan"]

        # Справочная строка
        ym_total_order = int(ym_plan["order"].sum())
        ym_total_price = (ym_plan["order"] * ym_plan["price"].fillna(0)).sum()
        ym_total_sku = ym_plan[ym_plan["order"] > 0]["sku"].nunique()
        ym_source = ym_result.get("source", "api")
        ym_source_label = "ЛК-файл" if ym_source == "lk" else "API"
        st.markdown(
            f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;'
            f'padding:14px 20px;margin:16px 0;font-size:15px;font-weight:600;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.04)">'
            f'К отгрузке: <b>{_fm(ym_total_order)}</b> шт · '
            f'<b>{_fm(ym_total_price)}</b> ₽ · '
            f'<b>{ym_total_sku}</b> SKU · '
            f'Глубина: <b>{ym_depth_used}</b> дней · '
            f'Источник: <b>{ym_source_label}</b></div>',
            unsafe_allow_html=True,
        )

        # Приоритет отгрузки
        st.markdown(
            '<div class="section-label"><span class="section-dot" style="background:#fc0"></span>'
            'ПРИОРИТЕТ ОТГРУЗКИ</div>', unsafe_allow_html=True)
        st.caption("Кластеры с минимальным запасом — грузим первыми")

        ym_cp_disp = ym_cp[["Кластер", "Дней запаса", "Заказать"]].copy()
        ym_cp_disp["Дней запаса"] = ym_cp_disp["Дней запаса"].astype(int)
        ym_cp_disp["Заказать"] = ym_cp_disp["Заказать"].astype(int)
        ym_cp_disp["Статус"] = ym_cp_disp["Дней запаса"].apply(_cluster_status)

        with st.container(border=True):
            st.dataframe(
                ym_cp_disp.style
                .format({"Заказать": "{:,.0f}"})
                .map(_cluster_color, subset=["Статус"])
                .map(_sc, subset=["Дней запаса"]),
                use_container_width=True, hide_index=True,
            )

        # Раскрытие по кластерам
        for _, row in ym_cp_disp.iterrows():
            cl_name = row["Кластер"]
            cl_plan = ym_plan[ym_plan["cluster"] == cl_name].copy()
            cl_plan = cl_plan[cl_plan["order"] > 0].sort_values("order", ascending=False)
            if cl_plan.empty:
                continue
            with st.expander(f"{cl_name} — {int(row['Дней запаса'])}д, заказать {int(row['Заказать'])} шт"):
                ct = cl_plan[["name", "sku", "stock_total", "daily", "days", "order"]].copy()
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
        import io as _io_ym
        ym_buf = _io_ym.BytesIO()
        ym_export = ym_plan[ym_plan["order"] > 0][
            ["name", "sku", "cluster", "stock_total", "daily", "days", "need", "quant", "order", "price"]
        ].copy()
        ym_export.columns = ["Название", "Артикул", "Кластер", "Остаток", "Прод/день",
                             "Дней запаса", "Потребность", "Квант", "Заказать", "Цена"]
        ym_export.to_excel(ym_buf, index=False, sheet_name="План ЯМ")
        st.download_button(
            "Скачать план поставок (Excel)",
            data=ym_buf.getvalue(),
            file_name=f"ym_supply_plan_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ym_supply_download",
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
            _qf = f"data/user_{_USER_ID}/quantum_stock.xlsx"
            result = compute_supply_data(days_plan=days_depth, creds=get_user_credentials(_USER_ID), quantum_file=_qf, user_id=_USER_ID)
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
        f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;'
        f'padding:14px 20px;margin:16px 0;font-size:15px;font-weight:600;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.04)">'
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
            '<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>'
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
                    st.markdown(f"**:red[Вампиры]** ({len(vampires)})")
                    st.caption("Получают сток с других кластеров")
                    for _, r in vampires.head(5).iterrows():
                        st.markdown(f"**{r['cluster']}** — +{r['balance']} шт")
                    if len(vampires) > 5:
                        with st.expander(f"Показать все (ещё {len(vampires) - 5})"):
                            for _, r in vampires.iloc[5:].iterrows():
                                st.markdown(f"**{r['cluster']}** — +{r['balance']} шт")
            with mc2:
                with st.container(border=True):
                    st.markdown(f"**:green[Доноры]** ({len(hamsters)})")
                    st.caption("Отправляют сток на другие кластеры")
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
    if st.button("Сформировать план (Excel)", key="supply_download"):
        with st.spinner("Формируется Excel..."):
            from ozon_supply import build_supply_plan
            _qf = f"data/user_{_USER_ID}/quantum_stock.xlsx"
            buf = build_supply_plan(days_plan=depth_used, creds=get_user_credentials(_USER_ID), quantum_file=_qf, user_id=_USER_ID)
            st.session_state["ozon_supply_excel"] = buf
    if st.session_state.get("ozon_supply_excel"):
        from datetime import datetime as _dt_dl
        st.download_button(
            "⬇️ Скачать план поставок Ozon (Excel)",
            data=st.session_state["ozon_supply_excel"].getvalue(),
            file_name=f"ozon_supply_plan_{_dt_dl.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ozon_supply_dl_btn",
        )


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

        st.markdown('<div class="section-label"><span class="section-dot" style="background:#2563eb"></span>Список акций</div>', unsafe_allow_html=True)
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
            st.markdown(f'<div class="section-label"><span class="section-dot" style="background:#7c3aed"></span>Акции ({len(promo_cols)})</div>', unsafe_allow_html=True)

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
