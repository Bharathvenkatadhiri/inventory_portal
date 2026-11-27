import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";

import { useCan } from "../../lib/session";

const TABS = [
  { to: "/inventory/items", label: "Items" },
  { to: "/inventory/warehouses", label: "Warehouses" },
  { to: "/inventory/stock", label: "Stock", needs: "stock.view" },
] as const;

export function InventoryLayout() {
  const can = useCan();
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Inventory">
        {TABS.filter((tab) => !("needs" in tab) || can(tab.needs)).map((tab) => (
          <Link
            key={tab.to}
            to={tab.to}
            className="-mb-px whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm font-semibold text-gray-500 hover:text-navy-900"
            activeProps={{ className: "border-accent-500 text-navy-900" }}
          >
            {tab.label}
          </Link>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}

// Shared by the inventory pages.

export function useInventoryOptions() {
  return useQuery({
    queryKey: ["inventory-options"],
    queryFn: async () => unwrap(await api.GET("/api/v1/inventory/options")),
    staleTime: Infinity,
  });
}

export function useItems(params: { q?: string; kind?: string } = {}) {
  return useQuery({
    queryKey: ["items", params],
    queryFn: async () => unwrap(await api.GET("/api/v1/items", { params: { query: params } })),
  });
}

export function useWarehouses(includeSystem = false) {
  return useQuery({
    queryKey: ["warehouses", includeSystem],
    queryFn: async () => unwrap(await api.GET("/api/v1/warehouses", { params: { query: { include_system: includeSystem } } })),
  });
}

/** Turns an optional number field's text into what the API takes ("" means none). */
export function optionalNumber(text: string): string | null {
  return text.trim() === "" ? null : text.trim();
}
