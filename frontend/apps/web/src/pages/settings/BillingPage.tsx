import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, ErrorText, Loading, Notice, PageHeader } from "../../components/ui";
import { day, plural, rupees } from "../../lib/format";
import { meKey } from "../../lib/session";

type PlanChoice = Schemas["PlanChoiceOut"];

const BUTTON: Record<string, string> = {
  subscribe: "Pay now",
  new: "Subscribe",
  upgrade: "Upgrade",
  schedule: "Switch at renewal",
  switch: "Switch trial",
  keep: "Keep this plan",
  renew: "Pay and renew",
};

const RETURN_MESSAGES: Record<string, { tone: "green" | "amber" | "red"; text: string }> = {
  paid: { tone: "green", text: "Payment received. Thank you!" },
  pending: { tone: "amber", text: "We're waiting for the payment to be confirmed. This page updates once it is." },
  failed: { tone: "red", text: "The payment didn't go through. Nothing was charged; you can try again." },
  stale: { tone: "amber", text: "Your subscription changed while you were paying. We'll refund that payment." },
};

export function BillingPage() {
  const queryClient = useQueryClient();
  const params = new URLSearchParams(window.location.search);
  const returned = RETURN_MESSAGES[params.get("status") ?? ""];
  const [message, setMessage] = useState<string | null>(null);
  const billing = useQuery({ queryKey: ["billing"], queryFn: async () => unwrap(await api.GET("/api/v1/billing")) });

  const afterChange = (result: Schemas["ActionOut"]) => {
    if (result.redirect_url) {
      window.location.assign(result.redirect_url);
      return;
    }
    setMessage(result.message ?? null);
    queryClient.invalidateQueries({ queryKey: ["billing"] });
    queryClient.invalidateQueries({ queryKey: meKey });
  };
  const choose = useMutation({
    mutationFn: async (plan: string) => unwrap(await api.POST("/api/v1/billing/plan", { body: { plan } })),
    onSuccess: afterChange,
  });
  const cancel = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/billing/cancel")), onSuccess: afterChange });
  const reactivate = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/billing/reactivate")), onSuccess: afterChange });

  if (billing.isLoading) return <Loading />;
  if (billing.error || !billing.data) return <ErrorText error={billing.error} />;
  const { subscription: sub, plans, payments, seats } = billing.data;

  return (
    <div className="space-y-6">
      <PageHeader title="Billing" description="One monthly price for your company. Prices exclude GST." />
      {returned ? <Notice tone={returned.tone}>{returned.text}</Notice> : null}
      {message ? <Notice tone="green">{message}</Notice> : null}
      <ErrorText error={choose.error ?? cancel.error ?? reactivate.error} />

      <Card title="Your subscription">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-2xl font-extrabold">{sub.plan_label}</p>
          <StatusBadge status={sub.status} />
          {sub.scheduled_plan ? <Badge tone="navy">Moves to {plans.find((p) => p.key === sub.scheduled_plan)?.label} at renewal</Badge> : null}
        </div>
        <p className="mt-2 text-sm text-gray-600">
          {sub.status === "trialing" && `Free trial until ${day(sub.trial_ends_at)}. Pay before then to keep making changes afterwards.`}
          {sub.status === "active" && (sub.auto_renew
            ? `Paid until ${day(sub.expires_at)}. Renews automatically for about ${rupees(sub.next_amount)}.`
            : `Paid until ${day(sub.expires_at)}. It won't renew.`)}
          {sub.status === "past_due" && `The renewal couldn't be charged. You can keep working until ${day(sub.grace_until)}.`}
          {sub.status === "lapsed" && "Your subscription has ended. Everyone can still see everything, but nothing can be changed until you pay."}
        </p>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          <div><dt className="text-xs text-gray-500">Plan, per month</dt><dd className="font-semibold">{rupees(sub.next_plan_amount)}</dd></div>
          <div>
            <dt className="text-xs text-gray-500">Extra light users</dt>
            <dd className="font-semibold">
              {sub.next_extra_light_users > 0
                ? `${sub.next_extra_light_users} × ${rupees(seats.extra_light_monthly)}`
                : `None (${plural(seats.light_included ?? 0, "light user")} included)`}
            </dd>
          </div>
          <div><dt className="text-xs text-gray-500">Next charge</dt><dd className="font-semibold">{rupees(sub.next_amount)}</dd></div>
        </dl>
        {sub.status === "active" ? (
          <div className="mt-4 border-t border-gray-100 pt-4">
            {sub.auto_renew ? (
              <button type="button" className="btn-link text-red-700" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel automatic renewal</button>
            ) : (
              <button type="button" className="btn-link" disabled={reactivate.isPending} onClick={() => reactivate.mutate()}>Turn automatic renewal back on</button>
            )}
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        {plans.map((plan) => (
          <PlanCard key={plan.key} plan={plan} current={plan.key === sub.plan} busy={choose.isPending} onChoose={() => choose.mutate(plan.key)} />
        ))}
      </div>

      <Card title="Payments">
        {payments.length === 0 ? (
          <p className="text-sm text-gray-500">No payments yet.</p>
        ) : (
          <table className="w-full">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Date</th>
                <th scope="col" className="table-head">What</th>
                <th scope="col" className="table-head">Period</th>
                <th scope="col" className="table-head text-right">Amount</th>
                <th scope="col" className="table-head">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {payments.map((p) => (
                <tr key={p.id}>
                  <td className="table-cell whitespace-nowrap">{day(p.paid_at ?? p.created_at)}</td>
                  <td className="table-cell">{p.kind_label}, {p.plan_label}{p.extra_light_users ? ` + ${plural(p.extra_light_users, "light user")}` : ""}</td>
                  <td className="table-cell whitespace-nowrap">{p.period_start ? `${day(p.period_start)} – ${day(p.period_end)}` : "—"}</td>
                  <td className="table-cell text-right tabular-nums">{rupees(p.amount)}</td>
                  <td className="table-cell"><Badge tone={p.status === "succeeded" ? "green" : p.status === "failed" ? "red" : "amber"}>{p.status_label}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "trialing") return <Badge tone="orange">Free trial</Badge>;
  if (status === "active") return <Badge tone="green">Active</Badge>;
  if (status === "past_due") return <Badge tone="red">Past due</Badge>;
  return <Badge tone="red">Ended</Badge>;
}

function PlanCard({ plan, current, busy, onChoose }: { plan: PlanChoice; current: boolean; busy: boolean; onChoose: () => void }) {
  const limits = [
    plan.users === null ? "Unlimited users" : `${plan.users} users`,
    plan.light_users === null ? "unlimited light users" : `${plan.light_users} light users`,
    plan.teams === null ? "unlimited teams" : plural(plan.teams, "team"),
    plan.warehouses === null ? "unlimited warehouses" : plural(plan.warehouses, "warehouse"),
  ].join(" · ");
  const label = BUTTON[plan.action];
  return (
    <div className={`card flex flex-col ${current ? "ring-2 ring-accent-500" : ""}`}>
      <div className="flex items-center justify-between">
        <p className="text-lg font-extrabold text-navy-900">{plan.label}</p>
        {current ? <Badge tone="orange">Your plan</Badge> : null}
      </div>
      <p className="mt-2 text-2xl font-extrabold">{rupees(plan.monthly)}<span className="text-sm font-medium text-gray-500"> / month</span></p>
      <p className="mt-1 text-xs text-gray-500">{limits}</p>
      <p className="mt-3 flex-1 text-sm text-gray-600">{plan.message}</p>
      {label ? (
        <button type="button" className={`${current ? "btn-primary" : "btn-secondary"} mt-4 w-full`} disabled={busy} onClick={onChoose}>
          {label}
          {(plan.action === "upgrade" || plan.action === "subscribe" || plan.action === "new" || plan.action === "renew") && Number(plan.amount) > 0
            ? ` · ${rupees(plan.amount)}` : ""}
        </button>
      ) : null}
    </div>
  );
}
