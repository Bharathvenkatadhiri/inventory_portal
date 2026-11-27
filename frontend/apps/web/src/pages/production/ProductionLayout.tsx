import { Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/production/entries", label: "Production entries" },
  { to: "/production/boms", label: "Bills of materials" },
] as const;

export function ProductionLayout() {
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Production">
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
