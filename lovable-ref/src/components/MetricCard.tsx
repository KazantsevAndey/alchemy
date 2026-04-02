import { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  brandColor: "ozon" | "wb" | "total";
  margin: number;
  revenue: string;
  profit: string;
  delay?: number;
  sparkline?: ReactNode;
}

const brandStyles = {
  ozon: { pill: "bg-ozon", label: "text-ozon" },
  wb: { pill: "bg-wb", label: "text-wb" },
  total: { pill: "bg-foreground", label: "text-foreground" },
};

export function MetricCard({ label, brandColor, margin, revenue, profit, delay = 0, sparkline }: MetricCardProps) {
  const style = brandStyles[brandColor];
  return (
    <div
      className="card-matte p-6 flex gap-4 opacity-0 animate-fade-in-up relative overflow-hidden"
      style={{ animationDelay: `${delay}ms` }}
    >
      {/* Pill */}
      <div className={`w-1 rounded-full self-stretch ${style.pill}`} />
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-semibold uppercase tracking-wider ${style.label}`}>{label}</p>
        <p className="metric-value text-3xl font-bold text-success mt-2">{margin.toFixed(1)}%</p>
        <p className="text-xs text-muted-foreground mt-0.5">маржа</p>
        <div className="mt-4 flex gap-6">
          <div>
            <p className="text-xs text-muted-foreground">Выручка</p>
            <p className="text-sm font-semibold text-foreground metric-value">{revenue}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Прибыль</p>
            <p className="text-sm font-semibold text-foreground metric-value">{profit}</p>
          </div>
        </div>
      </div>
      {sparkline && (
        <div className="absolute bottom-0 right-0 w-24 h-12 opacity-10">{sparkline}</div>
      )}
    </div>
  );
}
