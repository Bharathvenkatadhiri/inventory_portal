import { Link, Outlet } from "@tanstack/react-router";

import { useCan } from "../../lib/session";

const TABS = [
  { to: "/settings/company", label: "Company" },
  { to: "/settings/people", label: "People" },
  { to: "/settings/roles", label: "Roles" },
  { to: "/settings/teams", label: "Teams" },
  { to: "/settings/billing", label: "Billing", needs: "billing.manage" },
  { to: "/settings/audit", label: "Audit log", needs: "audit.view" },
] as const;

export function SettingsLayout() {
  const can = useCan();
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Settings">
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
