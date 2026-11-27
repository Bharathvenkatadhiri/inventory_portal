import { Link, Navigate, Outlet } from "@tanstack/react-router";

import { useCan } from "../../lib/session";

const TABS = [
  { to: "/production/entries", label: "Production entries", needs: ["production.record"] },
  { to: "/production/boms", label: "Bills of materials", needs: ["bom.manage", "production.record"] },
  { to: "/production/job-work", label: "Job work", needs: ["job_work.manage"] },
] as const;

/** /production: the first tab this person can use. */
export function ProductionIndex() {
  const can = useCan();
  const first = TABS.find((tab) => tab.needs.some((code) => can(code)));
  return <Navigate to={first?.to ?? "/production/entries"} replace />;
}

export function ProductionLayout() {
  const can = useCan();
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Production">
        {TABS.filter((tab) => tab.needs.some((code) => can(code))).map((tab) => (
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
