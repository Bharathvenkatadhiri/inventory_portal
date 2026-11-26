import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { PAYMENT_TONES } from "../accounts/AccountsLayout";
import { DocumentNotes } from "../notes/DocumentNotes";
import { NoteForm, type Kind } from "../notes/NoteForm";
import { RejectModal } from "./OrderDetailPage";
import { StatusBadge, TotalsTable } from "./PurchaseLayout";

type Bill = Schemas["BillOut"];

const FILTERS = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "pending_approval", label: "Waiting for approval" },
  { key: "posted", label: "Posted" },
  { key: "cancelled", label: "Cancelled" },
];

export function BillsPage() {
  const { bill: openId } = useSearch({ strict: false }) as { bill?: number };
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const bills = useQuery({
    queryKey: ["purchase-bills", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/purchase-bills", { params: { query: { status } } })),
  });
  const open = (bills.data ?? []).find((b) => b.id === openId);
  const show = (id: number | undefined) => navigate({ to: "/purchase/bills", search: id ? { bill: id } : {} });

  return (
    <div className="space-y-6">
      <PageHeader title="Bills" description="Suppliers' invoices, entered against what was received. Enter a bill from its purchase order." />
      <select className="field-input max-w-[220px]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
      <Card className="overflow-x-auto p-0">
        {bills.isLoading ? (
          <Loading />
        ) : bills.error ? (
          <div className="p-5"><ErrorText error={bills.error} /></div>
        ) : (bills.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title={status ? "No bills match" : "No bills yet"} /></div>
        ) : (
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Bill</th>
                <th scope="col" className="table-head">Supplier · invoice</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head">Due</th>
                <th scope="col" className="table-head text-right">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(bills.data ?? []).map((b) => (
                <tr key={b.id} className="cursor-pointer hover:bg-gray-50" onClick={() => show(b.id)}>
                  <td className="table-cell">
                    <button type="button" className="font-semibold text-navy-900 hover:underline">{b.number}</button>
                    <p className="text-xs text-gray-500">against {b.order_number}</p>
                  </td>
                  <td className="table-cell text-sm">{b.supplier.name}<p className="text-xs text-gray-500">{b.supplier_invoice_number} · {day(b.invoice_date)}</p></td>
                  <td className="table-cell">
                    <StatusBadge status={b.status} label={b.status_label} />
                    {b.payment_status ? <div className="mt-1"><Badge tone={PAYMENT_TONES[b.payment_status] ?? "gray"}>{b.payment_status_label}</Badge></div> : null}
                    {b.lines.some((l) => l.rate_differs) ? <div className="mt-1"><Badge tone="amber">Price differs from order</Badge></div> : null}
                  </td>
                  <td className="table-cell text-sm">{day(b.due_date)}</td>
                  <td className="table-cell text-right text-sm">{rupees(b.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open ? <BillDetail bill={open} onClose={() => show(undefined)} /> : null}
    </div>
  );
}

type Action = "submit" | "approve" | "cancel" | "delete";

function BillDetail({ bill, onClose }: { bill: Bill; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [noting, setNoting] = useState<Kind | null>(null);
  const navigate = useNavigate();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["purchase-bills"] });
    queryClient.invalidateQueries({ queryKey: ["purchase-order"] });
  };
  const act = useMutation({
    mutationFn: async (action: Action) => {
      const path = { params: { path: { bill_id: bill.id } } };
      switch (action) {
        case "submit": return unwrap(await api.POST("/api/v1/purchase-bills/{bill_id}/submit", path));
        case "approve": return unwrap(await api.POST("/api/v1/purchase-bills/{bill_id}/approve", path));
        case "cancel": return unwrap(await api.POST("/api/v1/purchase-bills/{bill_id}/cancel", path));
        case "delete": return unwrap(await api.DELETE("/api/v1/purchase-bills/{bill_id}", path));
      }
    },
    onSuccess: (_data, action) => {
      refresh();
      if (action === "delete") onClose();
    },
  });
  const a = bill.actions;
  return (
    <Modal wide title={`${bill.number} · ${bill.supplier.name}`} onClose={onClose} footer={
      <>
        {a.edit ? <button type="button" className="btn-danger mr-auto" onClick={() => { if (window.confirm("Delete this draft bill?")) act.mutate("delete"); }}>Delete</button> : null}
        {a.cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => { if (window.confirm("Cancel this bill? Its quantities can then be billed again.")) act.mutate("cancel"); }}>Cancel bill</button> : null}
        {bill.status === "posted" && a.cancel ? <button type="button" className="btn-secondary" onClick={() => setNoting("credit")}>Their credit note</button> : null}
        {bill.status === "posted" && a.cancel ? <button type="button" className="btn-secondary" onClick={() => setNoting("debit")}>Their debit note</button> : null}
        {a.approve ? <button type="button" className="btn-secondary" onClick={() => setRejecting(true)}>Send back</button> : null}
        {a.approve ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("approve")}>Approve and post</button> : null}
        {a.submit ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("submit")}>{bill.submit_needs_approval ? "Send for approval" : "Post"}</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={bill.status} label={bill.status_label} />
        <span>Invoice <strong>{bill.supplier_invoice_number}</strong> dated {day(bill.invoice_date)}</span>
        <span className="text-gray-500">· due {day(bill.due_date)}</span>
        <Link to="/purchase/orders/$orderId" params={{ orderId: String(bill.order_id) }} className="btn-link">{bill.order_number}</Link>
      </div>
      {bill.status === "draft" && bill.rejection_reason ? <Notice tone="amber">Sent back: {bill.rejection_reason}</Notice> : null}
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr>
            <th scope="col" className="table-head">Item</th>
            <th scope="col" className="table-head text-right">Quantity</th>
            <th scope="col" className="table-head text-right">Rate</th>
            <th scope="col" className="table-head text-right">Amount</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {bill.lines.map((l) => (
            <tr key={l.order_line_id}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code} · GST {Number(l.gst_rate)}%</span></td>
              <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
              <td className="table-cell text-right text-sm">
                {rupees(l.rate)}
                {l.rate_differs ? <p className="text-xs text-amber-700">Order rate {rupees(l.order_rate)}</p> : null}
              </td>
              <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TotalsTable totals={bill} />
      {bill.status === "posted" ? <DocumentNotes side="purchase" documentId={bill.id} adjustedTotal={bill.adjusted_total} total={bill.total} /> : null}
      {bill.status === "posted" ? (
        <p className="text-sm">Paid {rupees(bill.paid)} · outstanding <strong>{rupees(bill.outstanding)}</strong> <Badge tone={PAYMENT_TONES[bill.payment_status] ?? "gray"}>{bill.payment_status_label}</Badge></p>
      ) : null}
      <p className="text-xs text-gray-500">Entered by {bill.created_by || "—"}{bill.approved_by ? ` · posted by ${bill.approved_by}` : ""}</p>
      <ErrorText error={act.error} />
      {noting ? <NoteForm side="purchase" kind={noting} documentId={bill.id} onClose={() => setNoting(null)}
        onDone={(id) => navigate({ to: "/purchase/notes", search: { note: id } })} /> : null}
      {rejecting ? <RejectModal what="bill" onClose={() => setRejecting(false)} send={async (reason) => {
        unwrap(await api.POST("/api/v1/purchase-bills/{bill_id}/reject", { params: { path: { bill_id: bill.id } }, body: { reason } }));
        refresh();
        setRejecting(false);
      }} /> : null}
    </Modal>
  );
}
