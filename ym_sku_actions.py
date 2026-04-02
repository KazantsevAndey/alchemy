"""
Анализ SKU Яндекс Маркет — рекомендации по каждому товару.

Запускает ym_margin.py за февраль 2026, анализирует каждый SKU и выдаёт
конкретные рекомендации: снизить буст / поднять цену / вывести из ассортимента.
"""

import pandas as pd
from datetime import datetime

from config import PRICE_FILE
from utils.price_loader import load_price, build_cost_map
from ym_margin import load_orders, load_services, load_netting, calc_margin, SVC_ORDER


def analyze_skus():
    date_from = "2026-02-01"
    date_to = "2026-02-28"

    print("Загружаю данные за февраль 2026...")
    price = load_price(PRICE_FILE)
    cost_map = build_cost_map(price)

    sales = load_orders(date_from, date_to)
    costs_pivot, totals, grand = load_services(date_from, date_to)
    netting = load_netting(date_from, date_to)

    R = calc_margin(sales, costs_pivot, totals, cost_map, netting,
                    "Февраль 2026 — анализ SKU")

    # Рассчитаем доли расходов от полной цены
    for col in SVC_ORDER + ["Себес_итого"]:
        pct_col = f"%_{col}"
        R[pct_col] = R.apply(
            lambda r: r[col] / r["rev_full"] * 100 if r["rev_full"] > 0 else 0, axis=1
        )

    # Средняя цена за штуку
    R["avg_price"] = R.apply(
        lambda r: r["rev_full"] / r["qty"] if r["qty"] > 0 else 0, axis=1
    )
    R["avg_buyer_price"] = R.apply(
        lambda r: r["buyer_paid"] / r["qty"] if r["qty"] > 0 else 0, axis=1
    )

    # Буст на штуку
    R["boost_per_unit"] = R.apply(
        lambda r: r["Буст продаж"] / r["qty"] if r["qty"] > 0 else 0, axis=1
    )

    # Доставка на штуку
    R["delivery_per_unit"] = R.apply(
        lambda r: r["Доставка"] / r["qty"] if r["qty"] > 0 else 0, axis=1
    )

    # === Категоризация SKU ===
    actions = []
    for _, row in R.iterrows():
        sku = row["sku"]
        name = row["name"]
        qty = row["qty"]
        rev = row["rev_full"]
        margin_drr = row["Маржа_с_ДРР"]
        margin_no_drr = row["Маржа_без_ДРР"]
        margin_pct = row["%_маржи_с_ДРР"] * 100
        margin_no_drr_pct = row["%_маржи_без_ДРР"] * 100
        boost = row.get("Буст продаж", 0)
        boost_pct = row.get("%_Буст продаж", 0)
        delivery = row.get("Доставка", 0)
        delivery_pct = row.get("%_Доставка", 0)
        avg_price = row["avg_price"]
        sebes_pct = row.get("%_Себес_итого", 0)

        if qty == 0:
            action = "ВЫВЕСТИ"
            reason = "Нет продаж, только расходы на хранение"
            priority = 1
        elif margin_drr < 0 and margin_no_drr < 0:
            # Убыточен даже без буста
            if avg_price < 500:
                action = "ПОДНЯТЬ ЦЕНУ или ВЫВЕСТИ"
                reason = f"Убыток даже без буста. Низкая цена ({avg_price:.0f}₽), доставка {delivery_pct:.0f}% от цены"
            else:
                action = "ПОДНЯТЬ ЦЕНУ"
                reason = f"Убыток {margin_no_drr:.0f}₽ даже без буста. Проверить себестоимость"
            priority = 2
        elif margin_drr < 0 and margin_no_drr >= 0:
            # Убыточен из-за буста
            action = "СНИЗИТЬ/УБРАТЬ БУСТ"
            reason = f"Буст {boost:.0f}₽ ({boost_pct:.0f}%) делает товар убыточным. Без буста маржа +{margin_no_drr:.0f}₽"
            priority = 3
        elif margin_pct < 5:
            # Очень низкая маржа
            if boost_pct > 10:
                action = "СНИЗИТЬ БУСТ"
                reason = f"Маржа {margin_pct:.1f}% — критически низкая. Буст {boost_pct:.0f}% можно снизить"
            else:
                action = "НАБЛЮДАТЬ / ПОДНЯТЬ ЦЕНУ"
                reason = f"Маржа {margin_pct:.1f}% — на грани. Буст уже небольшой ({boost_pct:.0f}%)"
            priority = 4
        elif margin_pct < 15:
            if boost_pct > 15:
                action = "ОПТИМИЗИРОВАТЬ БУСТ"
                reason = f"Маржа {margin_pct:.1f}% нормальная, но буст {boost_pct:.0f}% высокий. Попробовать снизить на 30%"
            else:
                action = "ОК"
                reason = f"Маржа {margin_pct:.1f}%, буст {boost_pct:.0f}% — приемлемо"
            priority = 5
        else:
            action = "ОК — ПРИБЫЛЬНЫЙ"
            reason = f"Маржа {margin_pct:.1f}% — хороший товар"
            priority = 6

        actions.append({
            "SKU": sku,
            "Название": name,
            "Шт": qty,
            "Ср.цена": round(avg_price),
            "Выручка": round(rev),
            "Маржа_₽": round(margin_drr),
            "Маржа_%": round(margin_pct, 1),
            "Без_буста_%": round(margin_no_drr_pct, 1),
            "%Себес": round(sebes_pct, 1),
            "%Буст": round(boost_pct, 1),
            "%Доставка": round(delivery_pct, 1),
            "Действие": action,
            "Причина": reason,
            "_priority": priority,
        })

    df = pd.DataFrame(actions)
    df = df.sort_values(["_priority", "Маржа_₽"]).reset_index(drop=True)

    # === Вывод по категориям ===
    print("\n" + "=" * 80)
    print("ПЛАН ДЕЙСТВИЙ ПО SKU — ФЕВРАЛЬ 2026")
    print("=" * 80)

    categories = [
        (1, "ВЫВЕСТИ ИЗ АССОРТИМЕНТА (нет продаж, только расходы)"),
        (2, "ПОДНЯТЬ ЦЕНУ ИЛИ ВЫВЕСТИ (убыточны даже без буста)"),
        (3, "СНИЗИТЬ/УБРАТЬ БУСТ (убыточны из-за буста)"),
        (4, "НАБЛЮДАТЬ (очень низкая маржа)"),
        (5, "ОПТИМИЗИРОВАТЬ БУСТ (нормальная маржа, высокий буст)"),
        (6, "ОК — ПРИБЫЛЬНЫЕ"),
    ]

    total_loss_fixable = 0
    total_loss_structural = 0

    for prio, title in categories:
        subset = df[df["_priority"] == prio]
        if len(subset) == 0:
            continue

        total_margin = subset["Маржа_₽"].sum()
        total_rev = subset["Выручка"].sum()

        print(f"\n{'─' * 80}")
        print(f"{'🔴' if prio <= 2 else '🟡' if prio <= 4 else '🟢'} {title}")
        print(f"   {len(subset)} SKU | Выручка: {total_rev:,.0f}₽ | Маржа: {total_margin:,.0f}₽")
        print(f"{'─' * 80}")

        for _, r in subset.iterrows():
            marker = "  ❌" if r["Маржа_₽"] < 0 else "  ✅"
            print(f"{marker} {r['SKU'][:40]:40s}  {r['Шт']:>4} шт  "
                  f"цена {r['Ср.цена']:>6}₽  "
                  f"маржа {r['Маржа_₽']:>+8,.0f}₽ ({r['Маржа_%']:>+5.1f}%)  "
                  f"без буста {r['Без_буста_%']:>+5.1f}%")
            print(f"      └─ {r['Причина']}")

        if prio == 2:
            total_loss_structural = total_margin
        elif prio == 3:
            total_loss_fixable = total_margin

    # === Итоговые рекомендации ===
    total_margin_all = df["Маржа_₽"].sum()
    total_rev_all = df["Выручка"].sum()

    print(f"\n{'=' * 80}")
    print("ИТОГО")
    print(f"{'=' * 80}")
    print(f"Общая маржа:              {total_margin_all:>12,.0f}₽  ({total_margin_all/total_rev_all*100:.1f}%)")
    print(f"Убыток от структурно убыточных:  {total_loss_structural:>12,.0f}₽  (нужна цена↑ или вывод)")
    print(f"Убыток из-за буста:              {total_loss_fixable:>12,.0f}₽  (можно исправить снизив буст)")
    potential = total_margin_all - total_loss_structural - total_loss_fixable
    print(f"Потенциальная маржа (без убыточных): {potential:>10,.0f}₽")

    # Сохраняем в Excel
    out = df.drop(columns=["_priority"])
    out.to_excel("ym_sku_actions.xlsx", index=False)
    print(f"\nСохранено: ym_sku_actions.xlsx")


if __name__ == "__main__":
    analyze_skus()
