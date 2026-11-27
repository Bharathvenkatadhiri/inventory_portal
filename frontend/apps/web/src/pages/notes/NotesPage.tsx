import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { PdfButton } from "../../components/PdfButton";
import { Card, Empty, ErrorText, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { ReasonModal, StatusBadge, TotalsTable } from "../purchase/PurchaseLayout";
import type { Side } from "./NoteForm";
import { EInvoicePanel } from "../../components/GstPortal";

type Note = Schemas["NoteOut"];

export function SalesNotesPage() {
  return <NotesPage side="sales" />;
}

export function SupplierNotesPage() {
  return <NotesPage side="purchase" />;
}

function NotesPage({ side }: { side: Side }) {
  const { note: openId } = useSearch({ strict: false }) as { note?: number };
  const navigate = useNavigate();
  const sales = side === "sales";
  const [status, setStatus] = useState("");
  const notes = useQuery({
    queryKey: ["notes", side, status],
    queryFn: async () => unwrap(await api.GET("/api/v1/notes", { params: { query: { side, status } } })),
  });
  const open = (notes.data ?? []).find((n) => n.id === openId);
  const show = (id?: number) => navigate({ to: sales ? "/sales/notes" : "/purchase/notes", search: id ? { note: id } : {} });

  return (
    <div className="space-y-6">
      <PageHeader
        title={sales ? "Credit and debit notes" : "Supplier notes"}
        description={sales
          ? "Notes you issue to customers against your tax invoices: returns, discounts and rate corrections. Raise one from the invoice."
          : "Credit and debit notes your suppliers send you, recorded against their bills. Record one from the bill."}
      />
      <select className="field-input max-w-[200px]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">Any status</option>
        <option value="draft">Drafts</option>
        <option value="posted">Posted</option>
        <option value="cancelled">Cancelled</option>
      </select>
      <Card className="overflow-x-auto p-0">
        {notes.isLoading ? <Loading /> : notes.error ? <div className="p-5"><ErrorText error={notes.error} /></div> : (notes.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="No notes yet" /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Note</th>
                <th scope="col" className="table-head">{sales ? "Customer · invoice" : "Supplier · their invoice"}</th>
                <th scope="col" className="table-head">Reason</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head text-right">Amount</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(notes.data ?? []).map((n) => (
                <tr key={n.id} className="cursor-pointer hover:bg-gray-50" onClick={() => show(n.id)}>
                  <td className="table-cell">
                    <button type="button" className="font-semibold text-navy-900 hover:underline">{n.number || "Draft"}</button>
                    <p className="text-xs text-gray-500">{n.kind_label} · {day(n.note_date)}{n.party_note_number ? ` · their ${n.party_note_number}` : ""}</p>
                  </td>
                  <td className="table-cell text-sm">{n.party.name}<p className="text-xs text-gray-500">{n.document_number}</p></td>
                  <td className="table-cell text-sm">{n.reason_label}</td>
                  <td className="table-cell"><StatusBadge status={n.status} label={n.status_label} /></td>
                  <td className={`table-cell text-right text-sm font-semibold ${n.kind === "credit" ? "text-red-700" : ""}`}>{n.kind === "credit" ? "−" : "+"}{rupees(n.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open ? <NoteDetail note={open} onClose={() => show(undefined)} /> : null}
    </div>
  );
}

export function NoteDetail({ note: n, onClose }: { note: Note; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const refresh = () => { for (const key of ["notes", "sales-invoices", "purchase-bills", "outstanding", "stock", "items", "note-room"]) queryClient.invalidateQueries({ queryKey: [key] }); };
  const path = { params: { path: { note_id: n.id } } };
  const post = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/notes/{note_id}/post", path)), onSuccess: refresh });
  const remove = useMutation({ mutationFn: async () => unwrap(await api.DELETE("/api/v1/notes/{note_id}", path)), onSuccess: () => { refresh(); onClose(); } });
  const a = n.actions;
  return (
    <Modal wide title={`${n.kind_label} ${n.number || "(draft)"}`} onClose={onClose} footer={
      <>
        {a.edit ? <button type="button" className="btn-danger mr-auto" onClick={() => { if (window.confirm("Delete this draft?")) remove.mutate(); }}>Delete draft</button> : null}
        {a.cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => setCancelling(true)}>Cancel note</button> : null}
        {n.side === "sales" ? <PdfButton url={`/api/v1/notes/${n.id}/pdf`} label={n.status === "draft" ? "Preview PDF" : "PDF"} /> : null}
        {a.post ? <button type="button" className="btn-primary" disabled={post.isPending} onClick={() => post.mutate()}>Post</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={n.status} label={n.status_label} />
        <span>{n.reason_label} · dated {day(n.note_date)} · against {n.document_number} of {day(n.document_date)} · {n.party.name}</span>
      </div>
      {n.party_note_number ? <p className="text-sm">Supplier's note number: <strong>{n.party_note_number}</strong></p> : null}
      {n.warehouse ? <p className="text-sm text-gray-600">Goods {n.side === "sales" ? "came back into" : "went back from"} {n.warehouse}.</p> : null}
      {n.status === "draft" && n.side === "sales" ? <Notice tone="navy">It's numbered when you post it.</Notice> : null}
      {n.status === "cancelled" ? <Notice tone="red">Cancelled: {n.cancel_reason}</Notice> : null}
      {n.side === "sales" && n.status !== "draft" ? <EInvoicePanel kind="note" id={n.id} /> : null}
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Quantity</th><th scope="col" className="table-head text-right">Taxable</th><th scope="col" className="table-head text-right">GST</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {n.lines.map((l, i) => (
            <tr key={i}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code} · HSN {l.hsn_code || "—"}</span></td>
              <td className="table-cell text-right text-sm">{Number(l.quantity) ? quantity(l.quantity, l.unit) : "—"}</td>
              <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
              <td className="table-cell text-right text-sm">{Number(l.gst_rate)}% · {rupees(l.tax_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TotalsTable totals={n} />
      {n.remarks ? <p className="text-sm text-gray-600">{n.remarks}</p> : null}
      <p className="text-xs text-gray-500">Prepared by {n.created_by || "—"}{n.posted_by ? ` · posted by ${n.posted_by}` : ""}</p>
      <ErrorText error={post.error ?? remove.error} />
      {cancelling ? (
        <ReasonModal title="Cancel this note" label={n.reason === "goods_returned" ? "Why? The stock movement will be reversed." : "Why is it being cancelled?"}
          button="Cancel note" onClose={() => setCancelling(false)} send={async (reason) => {
            unwrap(await api.POST("/api/v1/notes/{note_id}/cancel", { ...path, body: { reason } }));
            refresh();
            setCancelling(false);
          }} />
      ) : null}
    </Modal>
  );
}
