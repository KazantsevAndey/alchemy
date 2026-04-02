import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

const data = [
  { name: "Футболка оверсайз", revenue: 284000, drr: 12.3 },
  { name: "Кроссовки спорт", revenue: 241000, drr: 8.7 },
  { name: "Худи базовое", revenue: 198000, drr: 15.1 },
  { name: "Джинсы slim", revenue: 176000, drr: 9.4 },
  { name: "Рюкзак городской", revenue: 152000, drr: 11.2 },
  { name: "Платье миди", revenue: 143000, drr: 7.8 },
  { name: "Куртка демисезон", revenue: 131000, drr: 14.6 },
  { name: "Шорты спортивные", revenue: 118000, drr: 6.3 },
  { name: "Сумка тоут", revenue: 107000, drr: 10.1 },
  { name: "Свитшот классик", revenue: 98000, drr: 13.5 },
  { name: "Юбка плиссе", revenue: 89000, drr: 8.9 },
  { name: "Брюки карго", revenue: 82000, drr: 11.7 },
  { name: "Поло базовое", revenue: 74000, drr: 9.8 },
  { name: "Ветровка лёгкая", revenue: 67000, drr: 12.1 },
  { name: "Шапка бини", revenue: 58000, drr: 7.2 },
];

export function TopSkuChart() {
  return (
    <div className="card-matte p-6 opacity-0 animate-fade-in-up" style={{ animationDelay: "400ms" }}>
      <p className="text-sm font-semibold text-foreground mb-1">Топ-15 SKU по выручке</p>
      <p className="text-xs text-muted-foreground mb-6">с указанием ДРР, %</p>
      <div className="h-[420px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 120, right: 40, top: 0, bottom: 0 }}>
            <XAxis type="number" tickFormatter={(v) => `${(v / 1000).toFixed(0)}К`} tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
            <YAxis dataKey="name" type="category" tick={{ fontSize: 12, fill: "#1e293b" }} axisLine={false} tickLine={false} width={120} />
            <Tooltip
              formatter={(value: number) => [`${value.toLocaleString("ru")} ₽`, "Выручка"]}
              contentStyle={{ borderRadius: 8, border: "1px solid hsl(var(--border))", fontSize: 12 }}
            />
            <Bar dataKey="revenue" radius={[0, 6, 6, 0]} barSize={18}>
              {data.map((entry, i) => (
                <Cell key={i} fill={i % 2 === 0 ? "#005BFF" : "#8B5CF6"} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
