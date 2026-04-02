import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

interface DonutItem {
  name: string;
  value: number;
}

interface DonutChartProps {
  title: string;
  data: DonutItem[];
  colors: string[];
}

export function DonutChart({ title, data, colors }: DonutChartProps) {
  return (
    <div className="card-matte p-6 opacity-0 animate-fade-in-up" style={{ animationDelay: "300ms" }}>
      <p className="text-sm font-semibold text-foreground mb-4">{title}</p>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={50}
              outerRadius={80}
              paddingAngle={3}
              dataKey="value"
              strokeWidth={0}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={colors[i % colors.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: 8,
                border: "1px solid hsl(var(--border))",
                fontSize: 12,
                fontFamily: "Plus Jakarta Sans",
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      {/* Legend */}
      <div className="mt-2 space-y-1.5">
        {data.map((item, i) => (
          <div key={item.name} className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: colors[i % colors.length] }} />
            <span className="truncate">{item.name}</span>
            <span className="ml-auto font-medium text-foreground metric-value">{item.value.toLocaleString("ru")} ₽</span>
          </div>
        ))}
      </div>
    </div>
  );
}
