import { useState } from "react";
import { MetricCard } from "@/components/MetricCard";
import { DonutChart } from "@/components/DonutChart";
import { Search } from "lucide-react";

const ozonColors = ["#005BFF", "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE"];
const expenses = [
  { name: "Логистика", value: 42000 },
  { name: "Хранение", value: 18000 },
  { name: "Реклама", value: 31000 },
  { name: "Комиссия", value: 26000 },
  { name: "Эквайринг", value: 8000 },
];

const charges = [
  { date: "24.05", type: "Продажа", amount: 84200, article: "OZ-1024" },
  { date: "24.05", type: "Возврат", amount: -3200, article: "OZ-0871" },
  { date: "23.05", type: "Продажа", amount: 126400, article: "OZ-1102" },
  { date: "23.05", type: "Логистика", amount: -8900, article: "—" },
  { date: "22.05", type: "Реклама", amount: -14300, article: "—" },
  { date: "22.05", type: "Продажа", amount: 97100, article: "OZ-0994" },
];

const unitEcon = [
  { name: "Футболка оверсайз", article: "OZ-1024", price: 1890, recommended: 2100, margin: 34.2, logistics: 180, storage: 42 },
  { name: "Кроссовки спорт", article: "OZ-0871", price: 4590, recommended: 4800, margin: 28.7, logistics: 320, storage: 85 },
  { name: "Худи базовое", article: "OZ-1102", price: 2490, recommended: 2690, margin: 22.1, logistics: 210, storage: 56 },
  { name: "Джинсы slim", article: "OZ-0994", price: 3190, recommended: 3400, margin: 31.5, logistics: 240, storage: 68 },
  { name: "Рюкзак городской", article: "OZ-0756", price: 2790, recommended: 2990, margin: 19.8, logistics: 190, storage: 48 },
  { name: "Платье миди", article: "OZ-1201", price: 2190, recommended: 2390, margin: 38.4, logistics: 160, storage: 38 },
  { name: "Куртка демисезон", article: "OZ-0845", price: 5490, recommended: 5900, margin: 15.3, logistics: 380, storage: 120 },
  { name: "Шорты спортивные", article: "OZ-1340", price: 1290, recommended: 1490, margin: 41.2, logistics: 120, storage: 28 },
];

export default function OzonPage() {
  const [search, setSearch] = useState("");
  const filtered = unitEcon.filter(
    (item) =>
      item.name.toLowerCase().includes(search.toLowerCase()) ||
      item.article.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-12 max-w-[1400px]">
      <div>
        <h1 className="text-2xl font-bold text-foreground tracking-tight">Ozon</h1>
        <p className="text-sm text-muted-foreground mt-1">Детальная аналитика маркетплейса</p>
      </div>

      {/* Summary + Donut */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-6">
          <MetricCard label="Вчера" brandColor="ozon" margin={32.4} revenue="1.24М ₽" profit="401К ₽" delay={0} />
          <MetricCard label="Месяц" brandColor="ozon" margin={29.1} revenue="28.4М ₽" profit="8.3М ₽" delay={80} />
        </div>
        <DonutChart title="Структура расходов" data={expenses} colors={ozonColors} />
      </section>

      {/* Charges table */}
      <section>
        <p className="text-sm font-semibold text-foreground mb-4">Начисления</p>
        <div className="card-matte overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Дата</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Тип</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Артикул</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Сумма</th>
              </tr>
            </thead>
            <tbody>
              {charges.map((c, i) => (
                <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30 transition-colors">
                  <td className="px-5 py-3 text-muted-foreground">{c.date}</td>
                  <td className="px-5 py-3">{c.type}</td>
                  <td className="px-5 py-3 text-muted-foreground font-mono text-xs">{c.article}</td>
                  <td className={`px-5 py-3 text-right font-semibold metric-value ${c.amount >= 0 ? "text-success" : "text-destructive"}`}>
                    {c.amount >= 0 ? "+" : ""}{c.amount.toLocaleString("ru")} ₽
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Unit economics */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm font-semibold text-foreground">Юнит-экономика</p>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
            <input
              type="text"
              placeholder="Поиск по названию или артикулу..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 pr-4 py-2 text-sm bg-card border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-ozon/20 focus:border-ozon w-72 transition-all placeholder:text-muted-foreground"
            />
          </div>
        </div>
        <div className="card-matte overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Товар</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Цена</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Рекоменд.</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider w-40">Маржа</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Логист.</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Хранен.</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, i) => (
                <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30 transition-colors">
                  <td className="px-5 py-3">
                    <p className="font-medium text-foreground">{item.name}</p>
                    <p className="text-xs text-muted-foreground font-mono">{item.article}</p>
                  </td>
                  <td className="px-5 py-3 text-right metric-value">{item.price.toLocaleString("ru")} ₽</td>
                  <td className="px-5 py-3 text-right metric-value text-ozon">{item.recommended.toLocaleString("ru")} ₽</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                        <div
                          className="h-full bg-success rounded-full transition-all"
                          style={{ width: `${Math.min(item.margin, 100)}%` }}
                        />
                      </div>
                      <span className="text-xs font-semibold text-success metric-value whitespace-nowrap">{item.margin}%</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-right text-muted-foreground">{item.logistics} ₽</td>
                  <td className="px-5 py-3 text-right text-muted-foreground">{item.storage} ₽</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
