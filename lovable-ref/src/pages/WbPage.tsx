import { MetricCard } from "@/components/MetricCard";
import { DonutChart } from "@/components/DonutChart";

const wbColors = ["#8B5CF6", "#A78BFA", "#C4B5FD", "#DDD6FE", "#EDE9FE"];
const expenses = [
  { name: "Логистика", value: 38000 },
  { name: "Хранение", value: 22000 },
  { name: "Реклама", value: 27000 },
  { name: "Комиссия", value: 19000 },
  { name: "Штрафы", value: 5000 },
];

export default function WbPage() {
  return (
    <div className="space-y-12 max-w-[1400px]">
      <div>
        <h1 className="text-2xl font-bold text-foreground tracking-tight">Wildberries</h1>
        <p className="text-sm text-muted-foreground mt-1">Детальная аналитика маркетплейса</p>
      </div>
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-6">
          <MetricCard label="Вчера" brandColor="wb" margin={28.7} revenue="980К ₽" profit="281К ₽" delay={0} />
          <MetricCard label="Месяц" brandColor="wb" margin={25.3} revenue="21.7М ₽" profit="5.5М ₽" delay={80} />
        </div>
        <DonutChart title="Структура расходов" data={expenses} colors={wbColors} />
      </section>
    </div>
  );
}
