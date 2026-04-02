import { NavLink, useLocation } from "react-router-dom";
import { FlaskConical, LayoutDashboard, Package, ShoppingCart, Truck } from "lucide-react";

const navItems = [
  { title: "Сводка", path: "/", icon: LayoutDashboard },
  { title: "Ozon", path: "/ozon", icon: ShoppingCart },
  { title: "WB", path: "/wb", icon: Package },
  { title: "Поставки", path: "/supplies", icon: Truck },
];

export function AppSidebar() {
  const location = useLocation();

  return (
    <aside className="w-[280px] min-h-screen bg-sidebar-bg flex flex-col shrink-0">
      {/* Logo */}
      <div className="px-6 py-8 flex items-center gap-3">
        <FlaskConical className="h-7 w-7 text-ozon" strokeWidth={1.5} />
        <span className="text-xl font-bold text-sidebar-fg tracking-tight">Alchemy</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 space-y-1">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors duration-150
                ${isActive
                  ? "bg-sidebar-active/10 text-sidebar-fg"
                  : "text-sidebar-muted hover:text-sidebar-fg hover:bg-sidebar-active/5"
                }`}
            >
              <item.icon className="h-4.5 w-4.5" strokeWidth={1.5} />
              {item.title}
            </NavLink>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-6 py-6">
        <p className="text-xs text-sidebar-muted">© 2026 Alchemy</p>
      </div>
    </aside>
  );
}
