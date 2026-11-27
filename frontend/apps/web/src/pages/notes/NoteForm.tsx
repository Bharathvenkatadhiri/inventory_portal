import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ErrorText, Field, Loading, Modal, Notice } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useWarehouses } from "../inventory/InventoryLayout";
import { todayIso } from "../purchase/PurchaseLayout";

export type Side = "sales" | "purchase";
export type Kind = "credit" | "debit";

/** A credit or debit note against one posted invoice (sales) or bill (purchase). */
export function NoteForm({ side, kind, documentId, onClose, onDone }: {
  side: Side; kind: Kind; documentId: number; onClose: () => void; onDone: (noteId: number) => void;
}) {
  const queryClient = useQueryClient();
  const room = useQuery({
    queryKey: ["note-room", side, documentId],
    queryFn: async () => unwrap(await api.GET("/api/v1/notes/room", { params: { query: { side, document_id: documentId } } })),
  });
  const warehouses = useWarehouses(side === "purchase");
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active && (w.kind === "store" || w.kind === "rejected"));
  const reasons = (kind === "credit" ? room.data?.credit_reasons : room.data?.debit_reasons) ?? [];
  const [reason, setReason] = useState<string>(kind === "credit" ? "goods_returned" : "rate_change");
  const [values, setValues] = useState<Record<number, string>>({});
  const [noteDate, setNoteDate] = useState(todayIso());
  const [warehouseId, setWarehouseId] = useState<number | null>(null);
  const [partyNumber, setPartyNumber] = useState("");
  const [remarks, setRemarks] = useState("");
  const isReturn = reason === "goods_returned";
  const warehouse = warehouseId ?? activeWarehouses.find((w) => w.is_default)?.id ?? activeWarehouses[0]?.id ?? null;
  const sales = side === "sales";
  const title = sales ? (kind === "credit" ? "Issue a credit note" : "Issue a debit note") : (kind === "credit" ? "Record the supplier's credit note" : "Record the supplier's debit note");

  const save = useMutation({
    mutationFn: async (post: boolean) => {
      const lines = (room.data?.lines ?? []).filter((l) => Number(values[l.line_id]) > 0).map((l) => (
        isReturn ? { line_id: l.line_id, quantity: values[l.line_id], taxable_value: "0" } : { line_id: l.line_id, quantity: "0", taxable_value: values[l.line_id] }
      ));
      const note = unwrap(await api.POST("/api/v1/notes", {
        body: { side, kind, document_id: documentId, reason, note_date: noteDate, warehouse_id: isReturn ? warehouse : null,
                party_note_number: partyNumber, remarks, lines },
      }));
      return post ? unwrap(await api.POST("/api/v1/notes/{note_id}/post", { params: { path: { note_id: note.id } } })) : note;
    },
    onSuccess: (note) => {
      for (const key of ["notes", "sales-invoices", "purchase-bills", "outstanding", "stock", "items", "note-room"]) queryClient.invalidateQueries({ queryKey: [key] });
      onDone(note.id);
    },
  });

  if (room.isLoading || !room.data) return <Modal title={title} onClose={onClose}>{room.error ? <ErrorText error={room.error} /> : <Loading />}</Modal>;
  const r = room.data;
  const pastDeadline = sales && kind === "credit" && r.credit_deadline && todayIso() > r.credit_deadline;
  const preview = (r.lines ?? []).reduce((sum, l) => {
    const v = Number(values[l.line_id]) || 0;
    const taxable = isReturn ? v * Number(l.rate) : v;
    return sum + taxable * (1 + Number(l.gst_rate) / 100);
  }, 0);

  return (
    <Modal wide title={title} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-secondary" disabled={save.isPending} onClick={() => save.mutate(false)}>Save draft</button>
        <button type="button" className="btn-primary" disabled={save.isPending || !!pastDeadline} onClick={() => save.mutate(true)}>Post</button>
      </>
    }>
      <p className="text-sm text-gray-600">
        Against {sales ? "invoice" : "their invoice"} <strong>{r.document_number}</strong> of {day(r.document_date)} · {r.party.name}
      </p>
      {pastDeadline ? <Notice tone="red">Credit notes for this invoice had to be issued by {day(r.credit_deadline)} (CGST Act s.34).</Notice> : null}
      {sales && kind === "credit" && !pastDeadline && r.credit_deadline ? <p className="text-xs text-gray-500">Credit notes for this invoice can be issued until {day(r.credit_deadline)}.</p> : null}
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Reason">
          {(id) => (
            <select id={id} className="field-input" value={reason} onChange={(e) => { setReason(e.target.value); setValues({}); }}>
              {reasons.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Note date">{(id) => <input id={id} type="date" className="field-input" value={noteDate} max={todayIso()} onChange={(e) => setNoteDate(e.target.value)} />}</Field>
        {!sales ? (
          <Field label="Supplier's note number">{(id) => <input id={id} className="field-input uppercase" value={partyNumber} onChange={(e) => setPartyNumber(e.target.value)} maxLength={30} />}</Field>
        ) : null}
        {isReturn ? (
          <Field label={sales ? "Goods come back into" : "Goods go back from"}>
            {(id) => (
              <select id={id} className="field-input" value={warehouse ?? ""} onChange={(e) => setWarehouseId(Number(e.target.value))}>
                {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
          </Field>
        ) : null}
      </div>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr>
            <th scope="col" className="table-head">Item</th>
            <th scope="col" className="table-head text-right">Billed</th>
            <th scope="col" className="table-head text-right">{isReturn ? "Can still return" : kind === "credit" ? "Can still credit (₹)" : "Rate"}</th>
            <th scope="col" className="table-head w-40">{isReturn ? "Quantity returned" : "Amount before GST (₹)"}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {r.lines.map((l) => (
            <tr key={l.line_id}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code} · HSN {l.hsn_code || "—"} · GST {Number(l.gst_rate)}%</span></td>
              <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)} × {rupees(l.rate)}</td>
              <td className="table-cell text-right text-sm">{isReturn ? quantity(l.returnable, l.unit) : kind === "credit" ? rupees(l.creditable) : rupees(l.rate)}</td>
              <td className="table-cell">
                <input className="field-input" inputMode="decimal" aria-label={`${isReturn ? "Quantity returned" : "Amount"}, ${l.item_name}`}
                  value={values[l.line_id] ?? ""} onChange={(e) => setValues({ ...values, [l.line_id]: e.target.value })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-sm text-gray-600">
        About {rupees(preview)} including GST ({r.is_interstate ? "IGST" : "CGST + SGST"}). It {kind === "credit" ? "reduces" : "adds to"} what {sales ? `${r.party.name} owes you` : `you owe ${r.party.name}`}.
        {sales ? " The note is numbered when you post it." : ""}
      </p>
      <Field label="Remarks">{(id) => <input id={id} className="field-input" value={remarks} onChange={(e) => setRemarks(e.target.value)} maxLength={300} />}</Field>
      <ErrorText error={save.error} />
    </Modal>
  );
}
