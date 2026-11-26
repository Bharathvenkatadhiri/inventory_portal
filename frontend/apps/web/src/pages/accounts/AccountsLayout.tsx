import { Link, Outlet } from "@tanstack/react-router";

import { useActive } from "../../lib/session";

const TABS = [
  { to: "/accounts/receivables", label: "Receivables", side: "selling" },
  { to: "/accounts/payables", label: "Payables", side: "buying" },
  { to: "/accounts/payments", label: "Payments" },
] as const;

export function AccountsLayout() {
  const { company } = useActive();
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Accounts">
        {TABS.filter((tab) => !("side" in tab) || (tab.side === "selling" ? company.selling_enabled : company.buying_enabled)).map((tab) => (
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

export type Direction = "received" | "made";

/** "Overdue 12 days" in red, "Paid" in green, and so on. */
export const PAYMENT_TONES: Record<string, "gray" | "green" | "amber" | "red" | "navy" | "orange"> = {
  unpaid: "gray",
  partly_paid: "orange",
  paid: "green",
  overdue: "red",
  credit: "navy",
};
