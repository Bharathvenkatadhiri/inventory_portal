import { csrfToken, tab, watchSessionInOtherTabs } from "@makesetu/api";
import { Link, Outlet } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { day } from "../lib/format";
import { useMe, useSwitchOrganization, type Active, type Me } from "../lib/session";
import { ErrorText, Loading, Notice } from "./ui";

const PURCHASE_ROLES = ["po.create", "po.approve", "grn.post", "purchase_bill.create", "purchase_bill.approve", "payment.record", "ledger.view"];
const ACCOUNTS_ROLES = ["payment.record", "payment.approve", "ledger.view"];
const SALES_ROLES = ["so.create", "so.approve", "quotation.create", "invoice.create", "dispatch.post"];

type NavItem = { to: string; label: string; exact: boolean; show?: (active: Active) => boolean };
const NAV: NavItem[] = [
  { to: "/", label: "Home", exact: true },
  { to: "/partners", label: "Partners", exact: false },
  {
    to: "/purchase", label: "Purchase", exact: false,
    show: (a) => a.company.buying_enabled && PURCHASE_ROLES.some((code) => a.allowed.includes(code)),
  },
  {
    to: "/sales", label: "Sales", exact: false,
    show: (a) => a.company.selling_enabled && SALES_ROLES.some((code) => a.allowed.includes(code)),
  },
  { to: "/inventory", label: "Inventory", exact: false },
  { to: "/accounts", label: "Accounts", exact: false, show: (a) => ACCOUNTS_ROLES.some((code) => a.allowed.includes(code)) },
  { to: "/settings", label: "Settings", exact: false },
];

// The modules the ERP is growing into, shown so people know what's coming.
const COMING = ["Production"];

export function AppShell() {
  const { data: me, error, isLoading } = useMe();
  const [menuOpen, setMenuOpen] = useState(false);
  // Why the tab reloaded on its own, if it did (another tab changed the session).
  const [notice] = useState(() => tab.takeNotice());

  // Sign-outs and sign-ins in other tabs take effect here straight away.
  useEffect(() => watchSessionInOtherTabs(), []);

  if (isLoading) return <Loading label="Loading MakeSetu…" />;
  if (error || !me) return <div className="mx-auto max-w-md p-8"><ErrorText error={error ?? "Couldn't load your account."} /></div>;
  if (!me.active) {
    return (
      <div className="mx-auto max-w-md p-8 text-sm text-gray-600">
        You aren't an active member of any organization. Ask your company's Org Admin for access.
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside
        className={`${menuOpen ? "flex" : "hidden"} fixed inset-y-0 left-0 z-40 w-64 flex-col border-r border-gray-200 bg-white lg:static lg:flex`}
        aria-label="Main"
      >
        <div className="flex h-16 items-center px-5">
          <a href="/app/" className="text-xl font-extrabold tracking-tight text-navy-900">
            Make<span className="text-accent-500">Setu</span>
          </a>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-2">
          {NAV.filter((item) => !item.show || item.show(me.active!)).map((item) => (
            <Link
              key={item.to}
              to={item.to}
              activeOptions={{ exact: item.exact }}
              onClick={() => setMenuOpen(false)}
              className="block rounded-lg px-3 py-2 text-sm font-semibold text-gray-600 hover:bg-navy-50 hover:text-navy-900"
              activeProps={{ className: "bg-navy-50 text-navy-900" }}
            >
              {item.label}
            </Link>
          ))}
          <p className="px-3 pb-1 pt-6 text-xs font-bold uppercase tracking-wide text-gray-400">Modules</p>
          {COMING.map((name) => (
            <span key={name} className="flex items-center justify-between rounded-lg px-3 py-2 text-sm text-gray-400">
              {name}
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500">Soon</span>
            </span>
          ))}
        </nav>
      </aside>
      {menuOpen ? <div className="fixed inset-0 z-30 bg-navy-950/30 lg:hidden" onClick={() => setMenuOpen(false)} /> : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center gap-3 border-b border-gray-200 bg-white px-4 sm:px-6">
          <button
            type="button"
            className="rounded p-1 text-gray-500 hover:text-navy-900 lg:hidden"
            aria-label="Open menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <CompanySwitcher me={me} active={me.active} />
          <div className="ml-auto">
            <UserMenu me={me} />
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
          {notice ? <div className="mb-4"><Notice tone="amber">{notice}</Notice></div> : null}
          <SubscriptionBanner active={me.active} />
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function CompanySwitcher({ me, active }: { me: Me; active: Active }) {
  const switchTo = useSwitchOrganization();
  const others = me.memberships.filter((m) => m.company_id !== active.company.id);
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="min-w-0">
        <p className="truncate text-sm font-bold text-navy-900">{active.company.name}</p>
        <p className="truncate text-xs text-gray-500">
          {active.role.name} · {active.subscription.plan_label}
        </p>
      </div>
      {others.length > 0 ? (
        <select
          aria-label="Switch company"
          className="field-input ml-2 w-auto py-1 text-xs"
          value=""
          disabled={switchTo.isPending}
          onChange={(event) => event.target.value && switchTo.mutate(Number(event.target.value))}
        >
          <option value="">Switch company…</option>
          {others.map((m) => (
            <option key={m.company_id} value={m.company_id}>
              {m.company_name} ({m.role_name})
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

function UserMenu({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: MouseEvent) => box.current && !box.current.contains(event.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const name = [me.user.first_name, me.user.last_name].filter(Boolean).join(" ") || me.user.email;
  return (
    <div className="relative" ref={box}>
      <button type="button" className="btn-secondary py-1.5" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="max-w-[10rem] truncate">{name}</span>
        <svg viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor" aria-hidden="true">
          <path d="M5.3 7.3a1 1 0 011.4 0L10 10.6l3.3-3.3a1 1 0 111.4 1.4l-4 4a1 1 0 01-1.4 0l-4-4a1 1 0 010-1.4z" />
        </svg>
      </button>
      {open ? (
        <div role="menu" className="absolute right-0 z-50 mt-2 w-56 rounded-xl border border-gray-200 bg-white p-1 shadow-lg">
          <p className="truncate px-3 py-2 text-xs text-gray-500">{me.user.email}</p>
          <Link to="/profile" role="menuitem" onClick={() => setOpen(false)} className="block rounded-lg px-3 py-2 text-sm hover:bg-navy-50">
            My profile
          </Link>
          <a href="/password-change/" role="menuitem" className="block rounded-lg px-3 py-2 text-sm hover:bg-navy-50">
            Change password
          </a>
          <form method="post" action="/logout/" onSubmit={() => tab.forget()}>
            <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken()} />
            <button type="submit" role="menuitem" className="block w-full rounded-lg px-3 py-2 text-left text-sm text-red-700 hover:bg-red-50">
              Sign out
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}

export function SubscriptionBanner({ active }: { active: Active }) {
  const sub = active.subscription;
  const canBill = active.allowed.includes("billing.manage");
  const action = (text: string): ReactNode =>
    canBill ? (
      <Link to="/settings/billing" className="font-semibold underline">
        {text}
      </Link>
    ) : (
      "Ask your Org Admin to choose a plan."
    );
  if (sub.status === "lapsed" || !sub.entitled) {
    return (
      <div role="status" className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
        Your subscription has ended. You can see everything, but nothing can be changed. {action("Choose a plan")}
      </div>
    );
  }
  if (sub.status === "past_due") {
    return (
      <div role="status" className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
        We couldn't renew your subscription. You can keep working until {day(sub.grace_until)}. {action("Pay now")}
      </div>
    );
  }
  if (sub.status === "trialing" && (sub.days_left ?? 99) <= 7) {
    return (
      <div role="status" className="mb-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        Your free trial of {sub.plan_label} ends on {day(sub.trial_ends_at)}. {action("Choose how to pay")}
      </div>
    );
  }
  return null;
}
