import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, ErrorText, Field, Modal } from "../../components/ui";
import { rupees } from "../../lib/format";

const TABS = [
  { to: "/purchase/rfqs", label: "RFQs" },
  { to: "/purchase/quotations-received", label: "Quotations received" },
  { to: "/purchase/orders", label: "Purchase orders" },
  { to: "/purchase/receipts", label: "Goods receipts" },
  { to: "/purchase/bills", label: "Bills" },
  { to: "/purchase/notes", label: "Supplier notes" },
] as const;

export function PurchaseLayout() {
  return (
    <div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-gray-200" aria-label="Purchase">
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

type Tone = "gray" | "green" | "amber" | "red" | "navy" | "orange";
const TONES: Record<string, Tone> = {
  draft: "gray",
  pending_approval: "amber",
  issued: "navy",
  partially_received: "orange",
  received: "green",
  closed: "gray",
  cancelled: "red",
  posted: "green",
  confirmed: "navy",
  partially_dispatched: "orange",
  dispatched: "green",
  accepted: "green",
  declined: "red",
  awaiting: "amber",
  sent: "navy",
  converted: "green",
  superseded: "gray",
};

export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <Badge tone={TONES[status] ?? "gray"}>{label}</Badge>;
}

export function orderKey(id: number | string) {
  return ["purchase-order", String(id)] as const;
}

export function useOrder(id: number | string, enabled = true) {
  return useQuery({
    queryKey: orderKey(id),
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/v1/purchase-orders/{order_id}", { params: { path: { order_id: Number(id) } } })),
  });
}

// The totals every order and bill carries (TotalsOut on the server, which the schema inlines).
type Totals = Pick<Schemas["BillOut"], "place_of_supply" | "is_interstate" | "taxable_value" | "cgst" | "sgst" | "igst" | "round_off" | "total">;

/** Taxable value, the GST split and the rounded total of an order or bill. */
export function TotalsTable({ totals }: { totals: Totals }) {
  const rows: [string, string | number][] = [["Taxable value", totals.taxable_value]];
  if (totals.is_interstate) rows.push(["IGST", totals.igst]);
  else rows.push(["CGST", totals.cgst], ["SGST", totals.sgst]);
  if (Number(totals.round_off) !== 0) rows.push(["Round off", totals.round_off]);
  return (
    <dl className="ml-auto w-full max-w-xs space-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between"><dt className="text-gray-500">{label}</dt><dd>{rupees(value)}</dd></div>
      ))}
      <div className="flex justify-between border-t border-gray-200 pt-1 font-semibold">
        <dt>Total</dt><dd>{rupees(totals.total)}</dd>
      </div>
      <p className="pt-1 text-xs text-gray-500">
        {totals.is_interstate ? "Inter-state supply: IGST." : "Supply within the state: CGST + SGST."}
        {totals.place_of_supply ? ` Place of supply: state ${totals.place_of_supply}.` : ""}
      </p>
    </dl>
  );
}

/** Today in the browser's time zone, as YYYY-MM-DD. */
export function todayIso(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

/** Asks for a reason (declining, cancelling, sending back) and passes it on. */
export function ReasonModal({ title, label, button, onClose, send }: {
  title: string;
  label: string;
  button: string;
  onClose: () => void;
  send: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const run = useMutation({ mutationFn: () => send(reason.trim()) });
  return (
    <Modal title={title} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={!reason.trim() || run.isPending} onClick={() => run.mutate()}>{button}</button>
      </>
    }>
      <Field label={label}>{(id) => <textarea id={id} className="field-input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />}</Field>
      <ErrorText error={run.error} />
    </Modal>
  );
}
