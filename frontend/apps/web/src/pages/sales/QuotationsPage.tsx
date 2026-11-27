import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { day, quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { ReasonModal, StatusBadge, todayIso, TotalsTable } from "../purchase/PurchaseLayout";

type Quotation = Schemas["QuotationOut"];

const quotationKey = (id: number | string) => ["quotation", String(id)] as const;

function useQuotation(id: number | string, enabled = true) {
  return useQuery({
    queryKey: quotationKey(id),
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/v1/quotations/{quotation_id}", { params: { path: { quotation_id: Number(id) } } })),
  });
}

/** Sales > Quotations: what we've offered customers. */
export function QuotationsPage() {
  const can = useCan();
  const writable = useWritable();
  const [status, setStatus] = useState("");
  const list = useQuery({
    queryKey: ["quotations", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/quotations", { params: { query: { status } } })),
  });
  return (
    <div className="space-y-6">
      <PageHeader
        title="Quotations"
        description="Offers to customers, valid until a date. Customers on MakeSetu see them under Purchase and can accept or decline; an accepted one becomes a sales order."
        actions={can("quotation.create") && writable ? <Link to="/sales/quotations/new" className="btn-primary">New quotation</Link> : null}
      />
      <select className="field-input w-56" aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All</option>
        <option value="draft">Drafts</option>
        <option value="sent">Sent, waiting for an answer</option>
        <option value="accepted">Accepted</option>
        <option value="converted">Sales order raised</option>
        <option value="declined">Declined</option>
      </select>
      <QuoteList rows={list.data} loading={list.isLoading} error={list.error} partyLabel="Customer" to="/sales/quotations/$quotationId" />
    </div>
  );
}

export function QuoteList({ rows, loading, error, partyLabel, to }: {
  rows: Schemas["QuoteListOut"][] | undefined; loading: boolean; error: unknown; partyLabel: string;
  to: "/sales/quotations/$quotationId" | "/purchase/quotations-received/$quotationId";
}) {
  return (
    <Card className="overflow-x-auto p-0">
      {loading ? <Loading /> : error ? <div className="p-5"><ErrorText error={error} /></div> : (rows ?? []).length === 0 ? (
        <div className="p-5"><Empty title="No quotations" /></div>
      ) : (
        <table className="w-full min-w-[640px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Quotation</th>
              <th scope="col" className="table-head">{partyLabel}</th>
              <th scope="col" className="table-head">Valid until</th>
              <th scope="col" className="table-head text-right">Total</th>
              <th scope="col" className="table-head">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(rows ?? []).map((q) => (
              <tr key={q.id} className="hover:bg-gray-50">
                <td className="table-cell">
                  <Link to={to} params={{ quotationId: String(q.id) }} className="font-semibold text-navy-900 hover:underline">{q.number}</Link>
                  <p className="text-xs text-gray-500">{day(q.quote_date)}</p>
                </td>
                <td className="table-cell text-sm">{q.party.name}</td>
                <td className="table-cell text-sm">{day(q.valid_until)}</td>
                <td className="table-cell text-right text-sm">{rupees(q.total)}</td>
                <td className="table-cell"><StatusBadge status={q.status === "expired" ? "declined" : q.status} label={q.status_label} />
                  {q.sales_order_number ? <p className="text-xs text-gray-500">{q.sales_order_number}</p> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/** A quotation's content: shared by the seller's page and the customer's. */
export function QuotationView({ q, seller }: { q: Quotation; seller: boolean }) {
  return (
    <>
      <Card>
        <dl className="grid gap-4 text-sm sm:grid-cols-3">
          <div><dt className="text-gray-500">{seller ? "Customer" : "From"}</dt><dd className="font-semibold">{seller ? q.customer.name : q.seller.name}</dd><dd className="text-xs text-gray-500">{(seller ? q.customer : q.seller).gstin}</dd></div>
          <div><dt className="text-gray-500">Dated · valid until</dt><dd>{day(q.quote_date)} · <span className={q.expired ? "font-semibold text-red-700" : ""}>{day(q.valid_until)}</span></dd></div>
          <div><dt className="text-gray-500">Enquiry</dt><dd>{q.enquiry_reference || "—"}</dd></div>
          <div><dt className="text-gray-500">Delivery</dt><dd>{q.delivery_days != null ? `${q.delivery_days} days from order` : "—"}</dd></div>
          <div><dt className="text-gray-500">Payment terms</dt><dd>{q.payment_terms_days != null ? `${q.payment_terms_days} days` : "—"}</dd></div>
          {seller ? <div><dt className="text-gray-500">Supply from</dt><dd>{q.dispatch_from}</dd></div> : null}
        </dl>
        {q.terms ? <p className="mt-3 whitespace-pre-line text-sm text-gray-700">{q.terms}</p> : null}
        {q.response_note ? <Notice tone={q.status === "declined" || q.status === "cancelled" ? "red" : "green"}>{q.status_label}{q.responded_by ? ` by ${q.responded_by}` : ""}: {q.response_note}</Notice> : null}
      </Card>
      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head text-right">Quantity</th>
              <th scope="col" className="table-head text-right">Rate</th>
              <th scope="col" className="table-head text-right">GST</th>
              <th scope="col" className="table-head text-right">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {q.lines.map((l) => (
              <tr key={l.id}>
                <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code}</span>{l.description ? <p className="text-xs text-gray-500">{l.description}</p> : null}</td>
                <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
                <td className="table-cell text-right text-sm">{rupees(l.rate)}</td>
                <td className="table-cell text-right text-sm">{Number(l.gst_rate)}%</td>
                <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-4"><TotalsTable totals={q.totals} /></div>
      </Card>
    </>
  );
}

export function QuotationDetailPage() {
  const { quotationId } = useParams({ strict: false }) as { quotationId: string };
  const quote = useQuotation(quotationId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [asking, setAsking] = useState<"decline" | "cancel" | null>(null);
  const refresh = (q?: Quotation) => {
    queryClient.invalidateQueries({ queryKey: ["quotations"] });
    if (q) queryClient.setQueryData(quotationKey(q.id), q);
  };
  const path = { params: { path: { quotation_id: Number(quotationId) } } };
  const act = useMutation({
    mutationFn: async (action: "send" | "revise" | "accepted" | "convert" | "delete") => {
      if (action === "send") return refresh(unwrap(await api.POST("/api/v1/quotations/{quotation_id}/send", path)));
      if (action === "accepted") return refresh(unwrap(await api.POST("/api/v1/quotations/{quotation_id}/response", { ...path, body: { accepted: true, note: "" } })));
      if (action === "revise") {
        const next = unwrap(await api.POST("/api/v1/quotations/{quotation_id}/revise", path));
        refresh(next);
        return navigate({ to: "/sales/quotations/$quotationId/edit", params: { quotationId: String(next.id) } });
      }
      if (action === "convert") {
        const made = unwrap(await api.POST("/api/v1/quotations/{quotation_id}/convert", path));
        refresh();
        queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
        return navigate({ to: "/sales/orders/$orderId", params: { orderId: String(made.sales_order_id) } });
      }
      unwrap(await api.DELETE("/api/v1/quotations/{quotation_id}", path));
      refresh();
      return navigate({ to: "/sales/quotations" });
    },
  });
  if (quote.isLoading) return <Loading />;
  if (quote.error || !quote.data) return <ErrorText error={quote.error} />;
  const q = quote.data;
  const a = q.actions;
  return (
    <div className="space-y-6">
      <PageHeader
        title={q.number}
        description={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={q.status === "expired" ? "declined" : q.status} label={q.status_label} /> Prepared by {q.prepared_by}.</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <PdfButton url={`/api/v1/quotations/${q.id}/pdf`} />
            {a.edit ? <Link to="/sales/quotations/$quotationId/edit" params={{ quotationId: String(q.id) }} className="btn-secondary">Edit</Link> : null}
            {a.edit ? <button type="button" className="btn-secondary" onClick={() => act.mutate("delete")}>Delete</button> : null}
            {a.send ? <button type="button" className="btn-primary" onClick={() => act.mutate("send")}>Send to customer</button> : null}
            {a.revise ? <button type="button" className="btn-secondary" onClick={() => act.mutate("revise")}>Revise</button> : null}
            {a.record_response ? <button type="button" className="btn-secondary" onClick={() => act.mutate("accepted")}>Customer accepted</button> : null}
            {a.record_response ? <button type="button" className="btn-secondary" onClick={() => setAsking("decline")}>Customer declined</button> : null}
            {a.convert ? <button type="button" className="btn-primary" onClick={() => act.mutate("convert")}>Raise sales order</button> : null}
            {a.cancel ? <button type="button" className="btn-danger" onClick={() => setAsking("cancel")}>Withdraw</button> : null}
          </div>
        }
      />
      <ErrorText error={act.error} />
      {q.sales_order_id ? (
        <Notice tone="green">Sales order <Link to="/sales/orders/$orderId" params={{ orderId: String(q.sales_order_id) }} className="btn-link">{q.sales_order_number}</Link> was raised from this quotation.</Notice>
      ) : null}
      {q.revisions.length ? (
        <p className="text-sm text-gray-600">Other revisions: {q.revisions.map((r, i) => (
          <span key={r.id}>{i ? ", " : ""}<Link to="/sales/quotations/$quotationId" params={{ quotationId: String(r.id) }} className="btn-link">{r.number}</Link> ({r.status_label.toLowerCase()})</span>
        ))}</p>
      ) : null}
      <QuotationView q={q} seller />
      {q.notes ? <Card title="Internal notes"><p className="whitespace-pre-line text-sm">{q.notes}</p></Card> : null}
      {asking ? (
        <ReasonModal title={asking === "decline" ? "Customer declined" : "Withdraw the quotation"}
          label={asking === "decline" ? "Why did they decline? (price, delivery…)" : "Why? The customer sees this."}
          button={asking === "decline" ? "Record" : "Withdraw"} onClose={() => setAsking(null)}
          send={async (reason) => {
            if (asking === "decline") refresh(unwrap(await api.POST("/api/v1/quotations/{quotation_id}/response", { ...path, body: { accepted: false, note: reason } })));
            else refresh(unwrap(await api.POST("/api/v1/quotations/{quotation_id}/cancel", { ...path, body: { reason } })));
            setAsking(null);
          }} />
      ) : null}
    </div>
  );
}

type Line = { key: number; item_id: number | null; quantity: string; rate: string; gst_rate: string; description: string };
let nextKey = 1;
const blankLine = (): Line => ({ key: nextKey++, item_id: null, quantity: "", rate: "", gst_rate: "", description: "" });

/** New (/sales/quotations/new) or a draft (/sales/quotations/:id/edit). */
export function QuotationEditor() {
  const { quotationId } = useParams({ strict: false }) as { quotationId?: string };
  const existing = useQuotation(quotationId ?? "0", !!quotationId);
  if (quotationId && existing.isLoading) return <Loading />;
  if (quotationId && existing.error) return <ErrorText error={existing.error} />;
  if (quotationId && existing.data && !existing.data.actions.edit) {
    return <Empty title="This quotation can't be changed">Only a draft can be edited; revise a sent one instead.</Empty>;
  }
  return <QuotationForm q={quotationId ? existing.data ?? null : null} />;
}

function plusDays(iso: string, days: number) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function QuotationForm({ q }: { q: Quotation | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const customers = useQuery({ queryKey: ["sales-customers"], queryFn: async () => unwrap(await api.GET("/api/v1/sales/customers")) });
  const items = useItems();
  const warehouses = useWarehouses();
  const activeItems = (items.data ?? []).filter((i) => i.is_active);
  const stores = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [customerId, setCustomerId] = useState<number | null>(q?.customer.id ?? null);
  const [fromId, setFromId] = useState<number | null>(q?.dispatch_from_id ?? null);
  const [dated, setDated] = useState(q?.quote_date ?? todayIso());
  const [validUntil, setValidUntil] = useState(q?.valid_until ?? plusDays(todayIso(), 30));
  const [enquiry, setEnquiry] = useState(q?.enquiry_reference ?? "");
  const [delivery, setDelivery] = useState(q?.delivery_days != null ? String(q.delivery_days) : "");
  const [terms, setTerms] = useState(q?.payment_terms_days != null ? String(q.payment_terms_days) : "");
  const [conditions, setConditions] = useState(q?.terms ?? "");
  const [notes, setNotes] = useState(q?.notes ?? "");
  const [lines, setLines] = useState<Line[]>(q ? q.lines.map((l) => ({
    key: nextKey++, item_id: l.item_id, quantity: String(Number(l.quantity)), rate: String(Number(l.rate)), gst_rate: String(Number(l.gst_rate)), description: l.description,
  })) : [blankLine()]);
  const warehouseId = fromId ?? stores.find((w) => w.is_default)?.id ?? stores[0]?.id ?? null;
  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pickItem = (key: number, itemId: number | null) => {
    const item = activeItems.find((i) => i.id === itemId);
    setLine(key, { item_id: itemId, rate: item?.sale_price != null ? String(Number(item.sale_price)) : "", gst_rate: item ? String(Number(item.gst_rate)) : "" });
  };
  const preview = lines.reduce((sum, l) => {
    const taxable = (Number(l.quantity) || 0) * (Number(l.rate) || 0);
    return { taxable: sum.taxable + taxable, tax: sum.tax + (taxable * (Number(l.gst_rate) || 0)) / 100 };
  }, { taxable: 0, tax: 0 });
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        customer_id: customerId!, dispatch_from_id: warehouseId!, quote_date: dated, valid_until: validUntil || null,
        enquiry_reference: enquiry, delivery_days: delivery ? Number(delivery) : null, payment_terms_days: terms ? Number(terms) : null,
        terms: conditions, notes,
        lines: lines.filter((l) => l.item_id !== null).map((l) => ({
          item_id: l.item_id!, quantity: l.quantity, rate: l.rate === "" ? null : l.rate, gst_rate: l.gst_rate === "" ? null : l.gst_rate, description: l.description,
        })),
      };
      return q ? unwrap(await api.PUT("/api/v1/quotations/{quotation_id}", { params: { path: { quotation_id: q.id } }, body }))
        : unwrap(await api.POST("/api/v1/quotations", { body }));
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["quotations"] });
      queryClient.setQueryData(quotationKey(saved.id), saved);
      navigate({ to: "/sales/quotations/$quotationId", params: { quotationId: String(saved.id) } });
    },
  });
  if (customers.isLoading || items.isLoading || warehouses.isLoading) return <Loading />;
  if ((customers.data ?? []).length === 0) {
    return <Empty title="No customers yet">Add the customer under Partners first (they don't need to be on MakeSetu). <Link to="/partners" className="btn-link">Go to Partners</Link></Empty>;
  }
  const ready = customerId !== null && warehouseId !== null && lines.some((l) => l.item_id !== null && Number(l.quantity) > 0);
  return (
    <div className="space-y-6">
      <PageHeader title={q ? `Edit ${q.number}` : "New quotation"} description="Saved as a draft. Send it from the quotation page." />
      <Card>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Customer">
            {(id) => (
              <select id={id} className="field-input" value={customerId ?? ""} onChange={(e) => {
                const next = e.target.value ? Number(e.target.value) : null;
                setCustomerId(next);
                const c = (customers.data ?? []).find((x) => x.id === next);
                if (c?.payment_terms_days != null && terms === "") setTerms(String(c.payment_terms_days));
              }}>
                <option value="">Choose a customer</option>
                {(customers.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}{c.gstin ? ` · ${c.gstin}` : ""}</option>)}
              </select>
            )}
          </Field>
          <Field label="Their enquiry" hint="Their RFQ number, or e.g. 'phone call of 20 Nov'.">{(id) => <input id={id} className="field-input" value={enquiry} maxLength={100} onChange={(e) => setEnquiry(e.target.value)} />}</Field>
          <Field label="Supply from">
            {(id) => (
              <select id={id} className="field-input" value={warehouseId ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
                {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Dated">{(id) => <input id={id} type="date" className="field-input" value={dated} onChange={(e) => setDated(e.target.value)} />}</Field>
          <Field label="Valid until">{(id) => <input id={id} type="date" className="field-input" value={validUntil} min={dated} onChange={(e) => setValidUntil(e.target.value)} />}</Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Delivery (days)">{(id) => <input id={id} className="field-input" inputMode="numeric" value={delivery} onChange={(e) => setDelivery(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Field label="Payment (days)">{(id) => <input id={id} className="field-input" inputMode="numeric" value={terms} onChange={(e) => setTerms(e.target.value.replace(/\D/g, ""))} />}</Field>
          </div>
        </div>
      </Card>
      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head w-32">Quantity</th>
              <th scope="col" className="table-head w-32">Rate (₹)</th>
              <th scope="col" className="table-head w-24">GST %</th>
              <th scope="col" className="table-head w-32 text-right">Amount</th>
              <th scope="col" className="table-head w-10"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.map((l, index) => {
              const item = activeItems.find((i) => i.id === l.item_id);
              return (
                <tr key={l.key}>
                  <td className="table-cell">
                    <select className="field-input" aria-label={`Item, line ${index + 1}`} value={l.item_id ?? ""} onChange={(e) => pickItem(l.key, e.target.value ? Number(e.target.value) : null)}>
                      <option value="">Choose an item</option>
                      {activeItems.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                    </select>
                    <input className="field-input mt-1 text-xs" placeholder="Specification or note (optional)" aria-label={`Description, line ${index + 1}`} value={l.description} maxLength={200} onChange={(e) => setLine(l.key, { description: e.target.value })} />
                  </td>
                  <td className="table-cell">
                    <div className="flex items-center gap-1">
                      <input className="field-input" inputMode="decimal" aria-label={`Quantity, line ${index + 1}`} value={l.quantity} onChange={(e) => setLine(l.key, { quantity: e.target.value })} />
                      <span className="text-xs text-gray-500">{item?.unit}</span>
                    </div>
                  </td>
                  <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Rate, line ${index + 1}`} value={l.rate} onChange={(e) => setLine(l.key, { rate: e.target.value })} /></td>
                  <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`GST rate, line ${index + 1}`} value={l.gst_rate} onChange={(e) => setLine(l.key, { gst_rate: e.target.value })} /></td>
                  <td className="table-cell text-right text-sm">{rupees((Number(l.quantity) || 0) * (Number(l.rate) || 0))}</td>
                  <td className="table-cell">{lines.length > 1 ? <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove line ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <button type="button" className="btn-link mt-3" onClick={() => setLines([...lines, blankLine()])}>+ Add a line</button>
        <dl className="ml-auto mt-4 w-full max-w-xs space-y-1 text-sm">
          <div className="flex justify-between"><dt className="text-gray-500">Taxable value</dt><dd>{rupees(preview.taxable)}</dd></div>
          <div className="flex justify-between"><dt className="text-gray-500">GST</dt><dd>{rupees(preview.tax)}</dd></div>
          <div className="flex justify-between border-t border-gray-200 pt-1 font-semibold"><dt>Total (before rounding)</dt><dd>{rupees(preview.taxable + preview.tax)}</dd></div>
        </dl>
      </Card>
      <Card>
        <Field label="Terms the customer sees" hint="Freight, packing, warranty, inspection…">{(id) => <textarea id={id} className="field-input" rows={3} value={conditions} onChange={(e) => setConditions(e.target.value)} />}</Field>
        <Field label="Internal notes" hint="Only your team sees these.">{(id) => <textarea id={id} className="field-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
      </Card>
      <ErrorText error={save.error} />
      <div className="flex justify-end gap-3">
        <Link to={q ? "/sales/quotations/$quotationId" : "/sales/quotations"} params={q ? { quotationId: String(q.id) } : {}} className="btn-secondary">Cancel</Link>
        <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Save draft</button>
      </div>
    </div>
  );
}
