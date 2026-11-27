import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/sales/rfqs-received", label: "RFQs received" },
  { to: "/sales/quotations", label: "Quotations" },
  { to: "/sales/orders-received", label: "Orders received" },
  { to: "/sales/orders", label: "Sales orders" },
  { to: "/sales/dispatches", label: "Dispatches" },
  { to: "/sales/invoices", label: "Invoices" },
  { to: "/sales/notes", label: "Credit / debit notes" },
] as const;

export function SalesLayout() {
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Sales">
        {TABS.map((tab) => (
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

export function salesOrderKey(id: number | string) {
  return ["sales-order", String(id)] as const;
}

export function useSalesOrder(id: number | string, enabled = true) {
  return useQuery({
    queryKey: salesOrderKey(id),
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/v1/sales-orders/{order_id}", { params: { path: { order_id: Number(id) } } })),
  });
}
