import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { day, quantity, rupees } from "../../lib/format";
import { PAYMENT_TONES } from "../accounts/AccountsLayout";
import { NoteForm, type Kind } from "../notes/NoteForm";
import { DocumentNotes } from "../notes/DocumentNotes";
import { ReasonModal, StatusBadge, TotalsTable } from "../purchase/PurchaseLayout";

type Invoice = Schemas["InvoiceOut"];

const FILTERS = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "posted", label: "Posted" },
  { key: "cancelled", label: "Cancelled" },
];

export function InvoicesPage() {
  const { invoice: openId } = useSearch({ strict: false }) as { invoice?: number };
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const invoices = useQuery({
    queryKey: ["sales-invoices", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/sales-invoices", { params: { query: { status } } })),
  });
  const open = (invoices.data ?? []).find((i) => i.id === openId);
  const show = (id: number | undefined) => navigate({ to: "/sales/invoices", search: id ? { invoice: id } : {} });

  return (
    <div className="space-y-6">
      <PageHeader title="Invoices" description="GST tax invoices to your customers, raised against what you've dispatched. Raise one from the sales order." />
      <select className="field-input max-w-[200px]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
      <Card className="overflow-x-auto p-0">
        {invoices.isLoading ? (
          <Loading />
        ) : invoices.error ? (
          <div className="p-5"><ErrorText error={invoices.error} /></div>
        ) : (invoices.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title={status ? "No invoices match" : "No invoices yet"} /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Invoice</th>
                <th scope="col" className="table-head">Customer</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head">Due</th>
                <th scope="col" className="table-head text-right">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(invoices.data ?? []).map((i) => (
                <tr key={i.id} className="cursor-pointer hover:bg-gray-50" onClick={() => show(i.id)}>
                  <td className="table-cell">
                    <button type="button" className="font-semibold text-navy-900 hover:underline">{i.number || "Draft"}</button>
                    <p className="text-xs text-gray-500">{day(i.invoice_date)} · {i.order_number}</p>
                  </td>
                  <td className="table-cell text-sm">{i.customer.name}<p className="text-xs text-gray-500">{i.customer.gstin}</p></td>
                  <td className="table-cell">
                    <StatusBadge status={i.status} label={i.status_label} />
                    {i.payment_status ? <div className="mt-1"><Badge tone={PAYMENT_TONES[i.payment_status] ?? "gray"}>{i.payment_status_label}</Badge></div> : null}
                  </td>
                  <td className="table-cell text-sm">{day(i.due_date)}</td>
                  <td className="table-cell text-right text-sm">{rupees(i.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open ? <InvoiceDetail invoice={open} onClose={() => show(undefined)} /> : null}
    </div>
  );
}

function InvoiceDetail({ invoice: inv, onClose }: { invoice: Invoice; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [noting, setNoting] = useState<Kind | null>(null);
  const navigate = useNavigate();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["sales-invoices"] });
    queryClient.invalidateQueries({ queryKey: ["sales-order"] });
  };
  const path = { params: { path: { invoice_id: inv.id } } };
  const post = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/sales-invoices/{invoice_id}/post", path)), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/sales-invoices/{invoice_id}", path)),
    onSuccess: () => { refresh(); onClose(); },
  });
  const a = inv.actions;
  return (
    <Modal wide title={inv.number ? `Tax invoice ${inv.number}` : "Draft invoice"} onClose={onClose} footer={
      <>
        {a.edit ? <button type="button" className="btn-danger mr-auto" onClick={() => { if (window.confirm("Delete this draft?")) remove.mutate(); }}>Delete draft</button> : null}
        {a.cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => setCancelling(true)}>Cancel invoice</button> : null}
        {a.cancel ? <button type="button" className="btn-secondary" onClick={() => setNoting("credit")}>Credit note</button> : null}
        {a.cancel ? <button type="button" className="btn-secondary" onClick={() => setNoting("debit")}>Debit note</button> : null}
        <PdfButton url={`/api/v1/sales-invoices/${inv.id}/pdf`} label={inv.status === "draft" ? "Preview PDF" : "PDF"} />
        {inv.status === "posted" ? <PdfButton url={`/api/v1/sales-invoices/${inv.id}/pdf?copies=3`} label="PDF, 3 copies" /> : null}
        {a.post ? <button type="button" className="btn-primary" disabled={post.isPending} onClick={() => post.mutate()}>Post invoice</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={inv.status} label={inv.status_label} />
        <span>Dated {day(inv.invoice_date)} · due {day(inv.due_date)}</span>
        <Link to="/sales/orders/$orderId" params={{ orderId: String(inv.order_id) }} className="btn-link">{inv.order_number}</Link>
      </div>
      {inv.status === "draft" ? <Notice tone="navy">The invoice number is given when it's posted.</Notice> : null}
      {inv.status === "cancelled" ? <Notice tone="red">Cancelled: {inv.cancel_reason}. The number {inv.number} stays used.</Notice> : null}
      <div className="grid gap-4 text-sm sm:grid-cols-2">
        <div><p className="text-xs font-bold uppercase text-gray-400">From</p><p className="font-semibold">{inv.seller.name}</p><p className="text-gray-600">GSTIN {inv.seller.gstin || "—"} · {inv.seller.state}</p></div>
        <div>
          <p className="text-xs font-bold uppercase text-gray-400">Bill to / ship to</p>
          <p className="font-semibold">{inv.customer.name}</p>
          <p className="text-gray-600">GSTIN {inv.customer.gstin || "—"}{inv.customer_po_number ? ` · their PO ${inv.customer_po_number}` : ""}</p>
          {inv.ship_to ? <p className="whitespace-pre-line text-gray-600">{inv.ship_to}</p> : null}
        </div>
      </div>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr>
            <th scope="col" className="table-head">Item</th>
            <th scope="col" className="table-head">HSN</th>
            <th scope="col" className="table-head text-right">Quantity</th>
            <th scope="col" className="table-head text-right">Rate</th>
            <th scope="col" className="table-head text-right">Taxable value</th>
            <th scope="col" className="table-head text-right">GST</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {inv.lines.map((l) => (
            <tr key={l.order_line_id}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code}</span></td>
              <td className="table-cell text-sm">{l.hsn_code || "—"}</td>
              <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
              <td className="table-cell text-right text-sm">{rupees(l.rate)}</td>
              <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
              <td className="table-cell text-right text-sm">{Number(l.gst_rate)}% · {rupees(l.tax_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TotalsTable totals={inv} />
      {inv.status === "posted" ? <DocumentNotes side="sales" documentId={inv.id} adjustedTotal={inv.adjusted_total} total={inv.total} /> : null}
      {inv.status === "posted" ? (
        <p className="text-sm">Received {rupees(inv.paid)} · outstanding <strong>{rupees(inv.outstanding)}</strong> <Badge tone={PAYMENT_TONES[inv.payment_status] ?? "gray"}>{inv.payment_status_label}</Badge></p>
      ) : null}
      <p className="text-xs text-gray-500">Prepared by {inv.created_by || "—"}{inv.posted_by ? ` · posted by ${inv.posted_by}` : ""}</p>
      <ErrorText error={post.error ?? remove.error} />
      {noting ? <NoteForm side="sales" kind={noting} documentId={inv.id} onClose={() => setNoting(null)}
        onDone={(id) => navigate({ to: "/sales/notes", search: { note: id } })} /> : null}
      {cancelling ? (
        <ReasonModal title={`Cancel ${inv.number}`} label="Why is it being cancelled?" button="Cancel invoice" onClose={() => setCancelling(false)} send={async (reason) => {
          unwrap(await api.POST("/api/v1/sales-invoices/{invoice_id}/cancel", { ...path, body: { reason } }));
          refresh();
          setCancelling(false);
        }} />
      ) : null}
    </Modal>
  );
}
