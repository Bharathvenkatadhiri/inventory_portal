import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Badge, Card, Meter } from "../components/ui";
import { day, rupees } from "../lib/format";
import { useActive, useCan, useMe } from "../lib/session";

function greeting(now = new Date()): string {
  const hour = now.getHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

const STATUS = {
  trialing: { label: "Free trial", tone: "orange" },
  active: { label: "Active", tone: "green" },
  past_due: { label: "Past due", tone: "red" },
  lapsed: { label: "Ended", tone: "red" },
} as const;

export function HomePage() {
  const { data: me } = useMe();
  const active = useActive();
  const can = useCan();
  const partners = useQuery({ queryKey: ["partners"], queryFn: async () => unwrap(await api.GET("/api/v1/partners")) });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: async () => unwrap(await api.GET("/api/v1/organization/permissions")),
  });
  const { seats, subscription: sub, company } = active;
  const status = STATUS[sub.status as keyof typeof STATUS] ?? STATUS.lapsed;

  const steps = [
    { done: seats.full + seats.light > 1 || seats.full_invited + seats.light_invited > 0, label: "Invite your team",
      detail: "Office staff as full users, shop-floor staff as light users.", to: "/settings/people", show: can("users.manage") },
    { done: seats.teams > 0, label: "Set up teams", detail: "Purchase, Store, QC… so approvals and alerts reach the right people.",
      to: "/settings/teams", show: can("users.manage") },
    { done: (partners.data?.length ?? 0) > 0, label: "Connect customers and suppliers",
      detail: "Find them on MakeSetu, or add them by GSTIN.", to: "/partners", show: can("partners.manage") },
    { done: sub.status === "active", label: "Choose how to pay",
      detail: sub.status === "trialing" ? `Your trial ends on ${day(sub.trial_ends_at)}.` : "Keep your subscription running.",
      to: "/settings/billing", show: can("billing.manage") },
  ].filter((step) => step.show);

  const granted = new Set(active.granted);
  const allowed = new Set(active.allowed);
  const mine = (permissions.data ?? []).filter((p) => granted.has(p.code));

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow">{company.name}</p>
        <h1 className="mt-1 text-2xl font-extrabold tracking-tight">
          {greeting()}, {me?.user.first_name || me?.user.email}
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          You're working as <span className="font-semibold text-navy-900">{active.role.name}</span>
          {active.role.user_type === "light" ? " (light user)" : ""}.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Subscription" className="lg:col-span-1">
          <div className="flex items-center gap-2">
            <p className="text-2xl font-extrabold">{sub.plan_label}</p>
            <Badge tone={status.tone}>{status.label}</Badge>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            {sub.status === "trialing" && `Free until ${day(sub.trial_ends_at)}.`}
            {sub.status === "active" && (sub.auto_renew ? `Renews ${day(sub.expires_at)} for ${rupees(sub.next_amount)}.` : `Ends ${day(sub.expires_at)}.`)}
            {sub.status === "past_due" && `Renewal failed. Access until ${day(sub.grace_until)}.`}
            {sub.status === "lapsed" && "Read-only until someone chooses a plan."}
          </p>
          {can("billing.manage") ? (
            <Link to="/settings/billing" className="btn-link mt-3">Manage billing →</Link>
          ) : null}
        </Card>

        <Card title="People and teams" className="lg:col-span-2">
          <div className="grid gap-4 sm:grid-cols-3">
            <Meter label="Users" used={seats.full + seats.full_invited} limit={seats.full_included}
              note={seats.full_invited ? `${seats.full_invited} invited, not joined yet` : undefined} />
            <Meter label="Light users" used={seats.light + seats.light_invited} limit={seats.light_included}
              note={seats.extra_light > 0 ? `${seats.extra_light} extra at ${rupees(seats.extra_light_monthly)}/month each` : "Extra ones are billed monthly"} />
            <Meter label="Teams" used={seats.teams} limit={seats.teams_included} />
          </div>
        </Card>
      </div>

      {steps.length > 0 ? (
        <Card title="Getting started" description="The few things that make MakeSetu useful from day one.">
          <ol className="divide-y divide-gray-100">
            {steps.map((step) => (
              <li key={step.label} className="flex items-start gap-3 py-3">
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${step.done ? "bg-green-600 text-white" : "border border-gray-300 text-gray-400"}`}
                  aria-hidden="true"
                >
                  {step.done ? "✓" : ""}
                </span>
                <div className="min-w-0 flex-1">
                  <p className={`text-sm font-semibold ${step.done ? "text-gray-500 line-through" : "text-navy-900"}`}>{step.label}</p>
                  <p className="text-sm text-gray-500">{step.detail}</p>
                </div>
                {!step.done ? <Link to={step.to} className="btn-link shrink-0">Start →</Link> : null}
              </li>
            ))}
          </ol>
        </Card>
      ) : null}

      <Card title="What your role allows" description="Your role decides what you can do; your company's plan and settings can hold some of it back.">
        {mine.length === 0 ? (
          <p className="text-sm text-gray-500">Your role has no permissions yet.</p>
        ) : (
          <ul className="grid gap-2 text-sm sm:grid-cols-2">
            {mine.map((p) => (
              <li key={p.code} className="flex items-start gap-2">
                {allowed.has(p.code) ? (
                  <span className="mt-0.5 text-green-600" aria-hidden="true">✓</span>
                ) : (
                  <span className="mt-0.5 text-gray-400" aria-hidden="true">–</span>
                )}
                <span className={allowed.has(p.code) ? "" : "text-gray-500"}>
                  <span className="text-gray-500">{p.module}:</span> {p.label}
                  {!allowed.has(p.code) ? (
                    <span className="ml-1 text-xs">
                      ({!p.included && p.needs_plan ? `needs ${p.needs_plan}` : !p.side_enabled ? `${p.side} is switched off` : "not available"})
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
