import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { ReasonModal, StatusBadge } from "../purchase/PurchaseLayout";

type Received = Schemas["RfqReceivedOut"];

/** Sales > RFQs received: customers asking us to quote, on MakeSetu. */
export function RfqsReceivedPage() {
  const list = useQuery({ queryKey: ["rfqs-received"], queryFn: async () => unwrap(await api.GET("/api/v1/sales/rfqs-received")) });
  const [quoting, setQuoting] = useState<Received | null>(null);
  const [declining, setDeclining] = useState<Received | null>(null);
  const queryClient = useQueryClient();
  return (
    <div className="space-y-6">
      <PageHeader title="RFQs received" description="Customers asking you to quote. Your rates go straight into their comparison; if they award you, their purchase order follows." />
      {list.isLoading ? <Loading /> : list.error ? <ErrorText error={list.error} /> : (list.data ?? []).length === 0 ? (
        <Empty title="No requests for quotation yet" />
      ) : (
        <div className="space-y-4">
          {(list.data ?? []).map((r) => (
            <Card key={r.invite_id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-semibold text-navy-900">{r.title} <span className="text-sm font-normal text-gray-500">{r.rfq_number}</span></p>
                  <p className="text-sm text-gray-500">
                    {r.buyer.name} · {day(r.rfq_date)}{r.respond_by ? ` · quote by ${day(r.respond_by)}` : ""}{r.required_by ? ` · needed by ${day(r.required_by)}` : ""}{r.deliver_to ? ` · to ${r.deliver_to}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={r.status === "awarded" || r.status === "quoted" ? "accepted" : r.status === "invited" ? "awaiting" : "declined"} label={r.status_label} />
                  {r.can_quote ? <button type="button" className="btn-primary" onClick={() => setQuoting(r)}>{r.status === "quoted" ? "Change quote" : "Quote"}</button> : null}
                  {r.can_quote && r.status !== "declined" ? <button type="button" className="btn-secondary" onClick={() => setDeclining(r)}>Won't quote</button> : null}
                </div>
              </div>
              <table className="mt-3 w-full">
                <thead className="border-b border-gray-100">
                  <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Quantity</th><th scope="col" className="table-head text-right">Your rate</th></tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {r.lines.map((l) => (
                    <tr key={l.rfq_line_id}>
                      <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code}{l.hsn_code ? ` · HSN ${l.hsn_code}` : ""}</span>
                        {l.description ? <p className="text-xs text-gray-500">{l.description}</p> : null}
                        {l.awarded_to_you === true ? <Badge tone="green">Awarded to you</Badge> : l.awarded_to_you === false ? <Badge>Not awarded</Badge> : null}</td>
                      <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
                      <td className="table-cell text-right text-sm">{l.rate != null ? `${rupees(l.rate)} + ${Number(l.gst_rate)}% GST` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {r.notes ? <p className="mt-2 whitespace-pre-line text-sm text-gray-600">{r.notes}</p> : null}
            </Card>
          ))}
        </div>
      )}
      {quoting ? <QuoteModal received={quoting} onClose={() => setQuoting(null)} /> : null}
      {declining ? (
        <ReasonModal title="Won't quote" label={`Tell ${declining.buyer.name} why (optional but helpful).`} button="Send"
          onClose={() => setDeclining(null)} send={async (reason) => {
            unwrap(await api.POST("/api/v1/sales/rfqs-received/{invite_id}/decline", { params: { path: { invite_id: declining.invite_id } }, body: { reason } }));
            queryClient.invalidateQueries({ queryKey: ["rfqs-received"] });
            setDeclining(null);
          }} />
      ) : null}
    </div>
  );
}

type QuoteRow = { rate: string; gst_rate: string };

/** A quote's lines and terms; used by suppliers here and by buyers entering one for a supplier. */
export function QuoteFields({ lines, rows, setRows, terms, setTerms }: {
  lines: { id: number; item_name: string; item_code: string; quantity: string | number; unit: string }[];
  rows: Record<number, QuoteRow>; setRows: (rows: Record<number, QuoteRow>) => void;
  terms: QuoteTerms; setTerms: (terms: QuoteTerms) => void;
}) {
  const set = (field: keyof QuoteTerms) => (e: { target: { value: string } }) => setTerms({ ...terms, [field]: e.target.value });
  return (
    <>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head w-32">Rate (₹, before GST)</th><th scope="col" className="table-head w-24">GST %</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {lines.map((l) => (
            <tr key={l.id}>
              <td className="table-cell text-sm">{quantity(l.quantity, l.unit)} {l.item_name} <span className="text-gray-500">{l.item_code}</span></td>
              <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Rate, ${l.item_name}`} placeholder="Not quoting" value={rows[l.id]?.rate ?? ""}
                onChange={(e) => setRows({ ...rows, [l.id]: { ...(rows[l.id] ?? { gst_rate: "" }), rate: e.target.value } })} /></td>
              <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`GST rate, ${l.item_name}`} value={rows[l.id]?.gst_rate ?? ""}
                onChange={(e) => setRows({ ...rows, [l.id]: { ...(rows[l.id] ?? { rate: "" }), gst_rate: e.target.value } })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Quote ref.">{(id) => <input id={id} className="field-input" value={terms.their_reference} maxLength={60} onChange={set("their_reference")} />}</Field>
        <Field label="Delivery (days)">{(id) => <input id={id} className="field-input" inputMode="numeric" value={terms.delivery_days} onChange={set("delivery_days")} />}</Field>
        <Field label="Payment (days)">{(id) => <input id={id} className="field-input" inputMode="numeric" value={terms.payment_terms_days} onChange={set("payment_terms_days")} />}</Field>
        <Field label="Valid until">{(id) => <input id={id} type="date" className="field-input" value={terms.valid_until} onChange={set("valid_until")} />}</Field>
      </div>
      <Field label="Remarks">{(id) => <input id={id} className="field-input" value={terms.remarks} maxLength={300} onChange={set("remarks")} />}</Field>
    </>
  );
}

export type QuoteTerms = { their_reference: string; delivery_days: string; payment_terms_days: string; valid_until: string; remarks: string };

export function quoteBody(lineIds: number[], rows: Record<number, QuoteRow>, terms: QuoteTerms) {
  return {
    their_reference: terms.their_reference, remarks: terms.remarks, valid_until: terms.valid_until || null,
    delivery_days: terms.delivery_days ? Number(terms.delivery_days) : null,
    payment_terms_days: terms.payment_terms_days ? Number(terms.payment_terms_days) : null,
    lines: lineIds.map((id) => ({ rfq_line_id: id, rate: rows[id]?.rate?.trim() || null, gst_rate: rows[id]?.gst_rate?.trim() || null })),
  };
}

function QuoteModal({ received: r, onClose }: { received: Received; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<Record<number, QuoteRow>>(Object.fromEntries(r.lines.map((l) => [l.rfq_line_id, {
    rate: l.rate != null ? String(Number(l.rate)) : "", gst_rate: l.gst_rate != null ? String(Number(l.gst_rate)) : "",
  }])));
  const [terms, setTerms] = useState<QuoteTerms>({
    their_reference: r.their_reference, delivery_days: r.delivery_days != null ? String(r.delivery_days) : "",
    payment_terms_days: r.payment_terms_days != null ? String(r.payment_terms_days) : "", valid_until: r.valid_until ?? "", remarks: r.remarks,
  });
  const send = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/sales/rfqs-received/{invite_id}/quote", {
      params: { path: { invite_id: r.invite_id } }, body: quoteBody(r.lines.map((l) => l.rfq_line_id), rows, terms),
    })),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["rfqs-received"] }); onClose(); },
  });
  return (
    <Modal wide title={`Quote to ${r.buyer.name} · ${r.rfq_number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={send.isPending} onClick={() => send.mutate()}>Send quote</button>
      </>
    }>
      <p className="text-sm text-gray-600">Leave a rate blank for anything you won't supply. GST left blank uses the item's rate.</p>
      <QuoteFields lines={r.lines.map((l) => ({ id: l.rfq_line_id, item_name: l.item_name, item_code: l.item_code, quantity: l.quantity, unit: l.unit }))}
        rows={rows} setRows={setRows} terms={terms} setTerms={setTerms} />
      <ErrorText error={send.error} />
    </Modal>
  );
}
