import { MetricCard } from "@/components/MetricCard";
import { DonutChart } from "@/components/DonutChart";
import { TopSkuChart } from "@/components/TopSkuChart";

const ozonExpenses = [
  { name: "Логистика", value: 42000 },
  { name: "Хранение", value: 18000 },
  { name: "Реклама", value: 31000 },
  { name: "Комиссия", value: 26000 },
  { name: "Эквайринг", value: 8000 },
];

const wbExpenses = [
  { name: "Логистика", value: 38000 },
  { name: "Хранение", value: 22000 },
  { name: "Реклама", value: 27000 },
  { name: "Комиссия", value: 19000 },
  { name: "Штрафы", value: 5000 },
];

const ozonColors = ["#005BFF", "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE"];
const wbColors = ["#8B5CF6", "#A78BFA", "#C4B5FD", "#DDD6FE", "#EDE9FE"];

export default function Dashboard() {
  return (
    <div className="space-y-12 max-w-[1400px]">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground tracking-tight">Обзор за 24 мая</h1>
        <p className="text-sm text-muted-foreground mt-1">Сводные метрики по всем маркетплейсам</p>
      </div>

      {/* Yesterday */}
      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-4">Вчера</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <MetricCard label="Ozon" brandColor="ozon" margin={32.4} revenue="1.24М ₽" profit="401К ₽" delay={0} />
          <MetricCard label="Wildberries" brandColor="wb" margin={28.7} revenue="980К ₽" profit="281К ₽" delay={80} />
          <MetricCard label="Итого" brandColor="total" margin={30.8} revenue="2.22М ₽" profit="682К ₽" delay={160} />
        </div>
      </section>

      {/* Month */}
      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-4">Месяц</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <MetricCard label="Ozon" brandColor="ozon" margin={29.1} revenue="28.4М ₽" profit="8.3М ₽" delay={240} />
          <MetricCard label="Wildberries" brandColor="wb" margin={25.3} revenue="21.7М ₽" profit="5.5М ₽" delay={320} />
          <MetricCard label="Итого" brandColor="total" margin={27.5} revenue="50.1М ₽" profit="13.8М ₽" delay={400} />
        </div>
      </section>

      {/* Charts */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <TopSkuChart />
        </div>
        <div className="space-y-6">
          <DonutChart title="Расходы Ozon" data={ozonExpenses} colors={ozonColors} />
          <DonutChart title="Расходы WB" data={wbExpenses} colors={wbColors} />
        </div>
      </section>
    </div>
  );
}
