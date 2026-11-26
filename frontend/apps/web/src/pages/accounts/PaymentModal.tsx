import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ErrorText, Field, Loading, Modal, Notice } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { todayIso } from "../purchase/PurchaseLayout";
import type { Direction } from "./AccountsLayout";

/** Record money received from a customer or paid to a supplier, and set it
 * against their open invoices or bills (oldest first by default). */
export function PaymentModal({ direction, partyId: initialParty, onClose }: { direction: Direction; partyId?: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const received = direction === "received";
  const options = useQuery({
    queryKey: ["payment-options", direction],
    queryFn: async () => unwrap(await api.GET("/api/v1/accounts/payment-options", { params: { query: { direction } } })),
  });
  const [partyId, setPartyId] = useState<number | null>(initialParty ?? null);
  const open = useQuery({
    queryKey: ["outstanding", direction, partyId],
    enabled: partyId !== null,
    queryFn: async () => unwrap(await api.GET("/api/v1/accounts/outstanding", { params: { query: { direction, party_id: partyId } } })),
  });
  const docs = (open.data?.documents ?? []).filter((d) => Number(d.available) > 0);
  const [amount, setAmount] = useState("");
  const [shares, setShares] = useState<Record<number, string>>({});
  const [paidOn, setPaidOn] = useState(todayIso());
  const [mode, setMode] = useState("bank");
  const [reference, setReference] = useState("");
  const [bankId, setBankId] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  const defaultBank = options.data?.bank_accounts.find((b) => b.is_default)?.id ?? options.data?.bank_accounts[0]?.id ?? null;

  /** Spreads `total` over the open documents, oldest due first. */
  const allocateOldestFirst = (total: number) => {
    let left = total;
    const next: Record<number, string> = {};
    for (const d of docs) {
      const take = Math.min(left, Number(d.available));
      next[d.id] = take > 0 ? take.toFixed(2) : "";
      left -= take;
    }
    setShares(next);
  };
  const allocated = Object.values(shares).reduce((sum, v) => sum + (Number(v) || 0), 0);
  const advance = (Number(amount) || 0) - allocated;

  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/payments", {
      body: {
        direction, party_id: partyId!, amount, payment_date: paidOn, mode, reference,
        bank_account_id: mode === "cash" ? null : bankId ?? defaultBank, notes,
        allocations: docs.filter((d) => Number(shares[d.id]) > 0).map((d) => ({ document_id: d.id, amount: shares[d.id] })),
      },
    })),
    onSuccess: () => {
      for (const key of ["outstanding", "payments", "sales-invoices", "purchase-bills"]) queryClient.invalidateQueries({ queryKey: [key] });
      onClose();
    },
  });

  if (options.isLoading) return <Modal title="Record a payment" onClose={onClose}><Loading /></Modal>;
  const ready = partyId !== null && Number(amount) > 0 && advance >= -0.005;

  return (
    <Modal wide title={received ? "Record money received" : "Record a payment to a supplier"} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>
          {!received && options.data?.needs_approval ? "Send for approval" : "Record"}
        </button>
      </>
    }>
      {!received && options.data?.needs_approval ? <Notice tone="amber">Payments you record wait for someone who can approve payments.</Notice> : null}
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label={received ? "From customer" : "To supplier"}>
          {(id) => (
            <select id={id} className="field-input" value={partyId ?? ""} onChange={(e) => { setPartyId(e.target.value ? Number(e.target.value) : null); setShares({}); }}>
              <option value="">Choose</option>
              {(options.data?.parties ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Amount (₹)">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={amount} onChange={(e) => { setAmount(e.target.value); allocateOldestFirst(Number(e.target.value) || 0); }} />}
        </Field>
        <Field label={received ? "Received on" : "Paid on"}>{(id) => <input id={id} type="date" className="field-input" value={paidOn} max={todayIso()} onChange={(e) => setPaidOn(e.target.value)} />}</Field>
        <Field label="How">
          {(id) => (
            <select id={id} className="field-input" value={mode} onChange={(e) => setMode(e.target.value)}>
              {(options.data?.modes ?? []).map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          )}
        </Field>
        <Field label={mode === "cheque" ? "Cheque number" : mode === "upi" ? "UPI reference" : mode === "cash" ? "Receipt / voucher no." : "UTR number"}>
          {(id) => <input id={id} className="field-input" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={60} />}
        </Field>
        {mode !== "cash" ? (
          <Field label={received ? "Into account" : "From account"}>
            {(id) => (
              <select id={id} className="field-input" value={bankId ?? defaultBank ?? ""} onChange={(e) => setBankId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Not recorded</option>
                {(options.data?.bank_accounts ?? []).map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
              </select>
            )}
          </Field>
        ) : null}
      </div>
      {mode === "cash" && received && Number(amount) >= 200000 ? <Notice tone="red">Cash receipts of ₹2,00,000 or more aren't allowed (s.269ST).</Notice> : null}
      {mode === "cash" && !received && Number(amount) > 10000 ? <Notice tone="amber">Cash payments over ₹10,000 a day to one party aren't deductible as an expense (s.40A(3)).</Notice> : null}

      {partyId !== null ? (
        open.isLoading ? <Loading /> : docs.length === 0 ? (
          <p className="text-sm text-gray-500">Nothing open for them right now; the whole amount will be held as an advance.</p>
        ) : (
          <table className="w-full">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">{received ? "Invoice" : "Their invoice"}</th>
                <th scope="col" className="table-head">Due</th>
                <th scope="col" className="table-head text-right">Left to settle</th>
                <th scope="col" className="table-head w-36">Settle now (₹)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {docs.map((d) => (
                <tr key={d.id}>
                  <td className="table-cell text-sm">{d.number}<p className="text-xs text-gray-500">{day(d.date)} · {d.order_number}</p></td>
                  <td className="table-cell text-sm">{day(d.due_date)}{d.status === "overdue" ? <p className="text-xs text-red-600">{d.status_label}</p> : null}</td>
                  <td className="table-cell text-right text-sm">{rupees(d.available)}</td>
                  <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Settle now, ${d.number}`} value={shares[d.id] ?? ""} onChange={(e) => setShares({ ...shares, [d.id]: e.target.value })} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}
      {Number(amount) > 0 ? (
        <p className={`text-sm ${advance < -0.005 ? "text-red-600" : "text-gray-600"}`}>
          Settling {rupees(allocated)}{advance > 0.005 ? `; ${rupees(advance)} held as an advance` : ""}{advance < -0.005 ? ` — more than the ${rupees(amount)} entered` : ""}.
        </p>
      ) : null}
      <Field label="Notes">{(id) => <input id={id} className="field-input" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={300} />}</Field>
      <ErrorText error={save.error} />
    </Modal>
  );
}
