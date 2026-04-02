import { Truck } from "lucide-react";

export default function SuppliesPage() {
  return (
    <div className="space-y-12 max-w-[1400px]">
      <div>
        <h1 className="text-2xl font-bold text-foreground tracking-tight">Поставки</h1>
        <p className="text-sm text-muted-foreground mt-1">Управление поставками на склады</p>
      </div>
      <div className="card-matte p-16 flex flex-col items-center justify-center text-center">
        <Truck className="h-12 w-12 text-muted-foreground/40 mb-4" strokeWidth={1.5} />
        <p className="text-lg font-semibold text-foreground">Раздел в разработке</p>
        <p className="text-sm text-muted-foreground mt-1">Здесь будет управление поставками и отслеживание грузов</p>
      </div>
    </div>
  );
}
