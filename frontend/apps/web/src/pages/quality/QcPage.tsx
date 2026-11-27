import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity } from "../../lib/format";
import { StatusBadge, todayIso } from "../purchase/PurchaseLayout";
import { batchText } from "../inventory/batches";

type Lot = Schemas["LotOut"];

const TONES: Record<string, string> = { pending: "pending_approval", partly: "partially_received", done: "received", cancelled: "cancelled" };

/** Goods waiting for QC, and the verdict on each lot. Laid out for a phone on the shop floor. */
export function QcPage() {
  const [status, setStatus] = useState("open");
  const lots = useQuery({
    queryKey: ["qc-lots", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/qc/lots", { params: { query: { status } } })),
  });
  const [inspecting, setInspecting] = useState<Lot | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="QC inspection"
        description="Goods received or made that need inspection wait here, under inspection, and can't be used yet. Passed goods move to their store; rejected goods go to Rejected goods, from where they can be returned to the supplier."
      />
      <select className="field-input max-w-[220px]" aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="open">Waiting for inspection</option>
        <option value="done">Inspected</option>
        <option value="all">All</option>
      </select>
      {lots.isLoading ? <Loading /> : lots.error ? <ErrorText error={lots.error} /> : (lots.data ?? []).length === 0 ? (
        <Empty title={status === "open" ? "Nothing waiting for inspection" : "No lots"} />
      ) : (
        <div className="space-y-3">
          {(lots.data ?? []).map((lot) => (
            <Card key={lot.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <button type="button" className="text-left" onClick={() => setOpenId(openId === lot.id ? null : lot.id)} aria-expanded={openId === lot.id}>
                  <p className="font-semibold text-navy-900">{lot.item_name} <span className="text-sm font-normal text-gray-500">{lot.item_code}</span></p>
                  <p className="text-sm text-gray-500">
                    {lot.number} · {lot.source_label} {lot.reference}{lot.supplier ? ` from ${lot.supplier}` : ""} · {day(lot.received_on)} · for {lot.destination}
                  </p>
                  {lot.batches.length ? (
                    <p className="font-mono text-xs text-gray-500">{lot.tracking === "serial" ? "S/N " : "Batch "}{batchText(lot.batches, lot.tracking)}</p>
                  ) : null}
                </button>
                <div className="flex items-center gap-3">
                  <div className="text-right text-sm">
                    <p><strong>{quantity(lot.pending_quantity, lot.unit)}</strong> to inspect</p>
                    {Number(lot.accepted_quantity) || Number(lot.rejected_quantity) ? (
                      <p className="text-xs text-gray-500">{quantity(lot.accepted_quantity)} passed · {quantity(lot.rejected_quantity)} rejected of {quantity(lot.quantity)}</p>
                    ) : null}
                  </div>
                  <StatusBadge status={TONES[lot.status] ?? lot.status} label={lot.status_label} />
                  {lot.can_inspect ? <button type="button" className="btn-primary" onClick={() => setInspecting(lot)}>Inspect</button> : null}
                </div>
              </div>
              {openId === lot.id && lot.inspections.length ? (
                <ul className="mt-3 divide-y divide-gray-100 border-t border-gray-100 pt-2 text-sm">
                  {lot.inspections.map((i, n) => (
                    <li key={n} className="py-1">
                      {day(i.inspected_on)} · {i.inspector}: {quantity(i.accepted)} passed, {quantity(i.rejected)} rejected{i.remarks ? ` — ${i.remarks}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Card>
          ))}
        </div>
      )}
      {inspecting ? <InspectForm lot={inspecting} onClose={() => setInspecting(null)} /> : null}
    </div>
  );
}

function InspectForm({ lot, onClose }: { lot: Lot; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [accepted, setAccepted] = useState(String(Number(lot.pending_quantity)));
  const [rejected, setRejected] = useState("");
  const [remarks, setRemarks] = useState("");
  const [on, setOn] = useState(todayIso());
  const [failed, setFailed] = useState<string[]>([]);
  const serial = lot.tracking === "serial";
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/qc/lots/{lot_id}/inspections", {
      params: { path: { lot_id: lot.id } }, body: { accepted: accepted || "0", rejected: rejected || "0", remarks, inspected_on: on, rejected_serials: serial ? failed : [] },
    })),
    onSuccess: () => {
      for (const key of ["qc-lots", "stock", "items"]) queryClient.invalidateQueries({ queryKey: [key] });
      onClose();
    },
  });
  const total = (Number(accepted) || 0) + (Number(rejected) || 0);
  const over = total > Number(lot.pending_quantity);
  return (
    <Modal title={`Inspect ${lot.item_name}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={total <= 0 || over || save.isPending} onClick={() => save.mutate()}>Record</button>
      </>
    }>
      <p className="text-sm text-gray-600">{lot.number} · {quantity(lot.pending_quantity, lot.unit)} waiting · passed goods go to {lot.destination}</p>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Passed">{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={accepted} onChange={(e) => setAccepted(e.target.value)} />}</Field>
        <Field label="Rejected">{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={rejected} onChange={(e) => setRejected(e.target.value)} />}</Field>
      </div>
      {serial && Number(rejected) > 0 ? (
        <fieldset>
          <legend className="text-sm font-semibold text-gray-700">Which ones failed? ({failed.length} of {quantity(rejected)} ticked)</legend>
          <div className="mt-1 grid max-h-40 grid-cols-2 gap-1 overflow-y-auto sm:grid-cols-3">
            {lot.batches.map((b) => (
              <label key={b.batch_id} className="flex items-center gap-2 font-mono text-xs">
                <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={failed.includes(b.number)}
                  onChange={(e) => setFailed(e.target.checked ? [...failed, b.number] : failed.filter((n) => n !== b.number))} />
                {b.number}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      {over ? <Notice tone="red">That's more than the {quantity(lot.pending_quantity, lot.unit)} waiting.</Notice> : null}
      <Field label={Number(rejected) > 0 ? "What was wrong (required)" : "Remarks"} hint="Defects found, measurements, sample size…">
        {(id) => <textarea id={id} className="field-input" rows={2} value={remarks} maxLength={300} onChange={(e) => setRemarks(e.target.value)} />}
      </Field>
      <Field label="Inspected on">{(id) => <input id={id} type="date" className="field-input" value={on} max={todayIso()} onChange={(e) => setOn(e.target.value)} />}</Field>
      <ErrorText error={save.error} />
    </Modal>
  );
}
