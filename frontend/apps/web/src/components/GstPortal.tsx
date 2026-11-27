import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { day, rupees } from "../lib/format";
import { Badge, ErrorText, Field, Modal, Notice } from "./ui";

type IrnState = Schemas["EInvoiceStateOut"];
type EwbState = Schemas["EwbStateOut"];

const IRN_REASONS = [["1", "Duplicate"], ["2", "Data entry mistake"], ["3", "Order cancelled"], ["4", "Other"]] as const;
const EWB_REASONS = [["1", "Duplicate"], ["2", "Order cancelled"], ["3", "Data entry mistake"], ["4", "Other"]] as const;

function when(iso: string) {
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** The IRN of a posted invoice or sales credit/debit note: register it, or cancel it within 24 hours. */
export function EInvoicePanel({ kind, id, onChange }: { kind: "invoice" | "note"; id: number; onChange?: () => void }) {
  const queryClient = useQueryClient();
  const key = ["einvoice", kind, id];
  const state = useQuery({
    queryKey: key,
    queryFn: async () => unwrap(kind === "invoice"
      ? await api.GET("/api/v1/sales-invoices/{invoice_id}/einvoice", { params: { path: { invoice_id: id } } })
      : await api.GET("/api/v1/notes/{note_id}/einvoice", { params: { path: { note_id: id } } })),
  });
  const done = (s: IrnState) => {
    queryClient.setQueryData(key, s);
    queryClient.invalidateQueries({ queryKey: ["gst-portal"] });
    onChange?.();
  };
  const register = useMutation({
    mutationFn: async () => unwrap(kind === "invoice"
      ? await api.POST("/api/v1/sales-invoices/{invoice_id}/einvoice", { params: { path: { invoice_id: id } } })
      : await api.POST("/api/v1/notes/{note_id}/einvoice", { params: { path: { note_id: id } } })),
    onSuccess: done,
  });
  const [cancelling, setCancelling] = useState(false);
  const s = state.data;
  if (!s || (!s.applicable && !s.current && s.history.length === 0)) return null;
  const e = s.current;
  return (
    <div className="rounded border border-gray-200 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-navy-900">E-invoice {e ? <Badge tone="green">IRN registered</Badge> : <Badge tone="amber">Not registered</Badge>}</p>
        <div className="flex gap-2">
          {s.can_register ? <button type="button" className="btn-primary" disabled={register.isPending} onClick={() => register.mutate()}>{register.isPending ? "Registering…" : "Get IRN"}</button> : null}
          {e?.can_cancel ? <button type="button" className="btn-secondary" onClick={() => setCancelling(true)}>Cancel IRN</button> : null}
        </div>
      </div>
      {e ? (
        <dl className="mt-2 space-y-1">
          <div><dt className="inline text-gray-500">IRN </dt><dd className="inline break-all font-mono text-xs">{e.irn}</dd></div>
          <div><dt className="inline text-gray-500">Ack no. </dt><dd className="inline font-mono">{e.ack_number}</dd> · {when(e.ack_date)} · by {e.registered_by}</div>
          <p className="text-xs text-gray-500">The IRN and signed QR code are printed on the PDF.{e.can_cancel ? " It can be cancelled until 24 hours after registering." : ""}</p>
          {e.provider === "mock" ? <p className="text-xs text-amber-700">Test portal: not a valid e-invoice until a GSP is connected.</p> : null}
        </dl>
      ) : !s.can_register && s.applicable ? <p className="mt-1 text-xs text-gray-500">Only posted invoices to registered (GSTIN) customers are e-invoiced.</p> : null}
      {s.history.map((h) => (
        <p key={h.id} className="mt-1 text-xs text-gray-500">Cancelled IRN {h.irn.slice(0, 12)}… ({h.cancel_reason_label}: {h.cancel_remark})</p>
      ))}
      <ErrorText error={register.error} />
      {cancelling ? (
        <CancelModal title="Cancel the IRN" reasons={IRN_REASONS} note="After cancelling, cancel the invoice too; its number can't be registered again."
          onClose={() => setCancelling(false)} send={async (reason, remark) => {
            const body = { reason, remark };
            done(unwrap(kind === "invoice"
              ? await api.POST("/api/v1/sales-invoices/{invoice_id}/einvoice/cancel", { params: { path: { invoice_id: id } }, body })
              : await api.POST("/api/v1/notes/{note_id}/einvoice/cancel", { params: { path: { note_id: id } }, body })));
            setCancelling(false);
          }} />
      ) : null}
    </div>
  );
}

function CancelModal({ title, reasons, note, onClose, send }: {
  title: string; reasons: readonly (readonly [string, string])[]; note: string; onClose: () => void; send: (reason: string, remark: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("2");
  const [remark, setRemark] = useState("");
  const run = useMutation({ mutationFn: () => send(reason, remark.trim()) });
  return (
    <Modal title={title} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-danger" disabled={!remark.trim() || run.isPending} onClick={() => run.mutate()}>Cancel it on the portal</button>
      </>
    }>
      <Field label="Reason">
        {(id) => <select id={id} className="field-input" value={reason} onChange={(e) => setReason(e.target.value)}>{reasons.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
      </Field>
      <Field label="Remark for the portal">{(id) => <input id={id} className="field-input" maxLength={100} value={remark} onChange={(e) => setRemark(e.target.value)} />}</Field>
      <p className="text-xs text-gray-500">{note}</p>
      <ErrorText error={run.error} />
    </Modal>
  );
}

type EwbKind = "dispatch" | "job-work";

async function ewbCall(kind: EwbKind, id: number, action: "get" | "generate" | "vehicle" | "cancel", body?: unknown) {
  const path = { params: { path: { document_id: id } } };
  if (kind === "dispatch") {
    if (action === "get") return unwrap(await api.GET("/api/v1/dispatches/{document_id}/eway-bill", path));
    if (action === "generate") return unwrap(await api.POST("/api/v1/dispatches/{document_id}/eway-bill", { ...path, body: body as Schemas["EwbIn"] }));
    if (action === "vehicle") return unwrap(await api.POST("/api/v1/dispatches/{document_id}/eway-bill/vehicle", { ...path, body: body as Schemas["EwbVehicleIn"] }));
    return unwrap(await api.POST("/api/v1/dispatches/{document_id}/eway-bill/cancel", { ...path, body: body as Schemas["EwbCancelIn"] }));
  }
  if (action === "get") return unwrap(await api.GET("/api/v1/job-work/{document_id}/eway-bill", path));
  if (action === "generate") return unwrap(await api.POST("/api/v1/job-work/{document_id}/eway-bill", { ...path, body: body as Schemas["EwbIn"] }));
  if (action === "vehicle") return unwrap(await api.POST("/api/v1/job-work/{document_id}/eway-bill/vehicle", { ...path, body: body as Schemas["EwbVehicleIn"] }));
  return unwrap(await api.POST("/api/v1/job-work/{document_id}/eway-bill/cancel", { ...path, body: body as Schemas["EwbCancelIn"] }));
}

/** The e-way bill for a delivery or job work challan: make it, change the vehicle, or cancel it within 24 hours. */
export function EwayBillPanel({ kind, id, vehicle = "" }: { kind: EwbKind; id: number; vehicle?: string }) {
  const queryClient = useQueryClient();
  const key = ["eway-bill", kind, id];
  const state = useQuery({ queryKey: key, queryFn: () => ewbCall(kind, id, "get") });
  const [making, setMaking] = useState(false);
  const [changing, setChanging] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const done = (s: EwbState) => {
    queryClient.setQueryData(key, s);
    queryClient.invalidateQueries({ queryKey: ["gst-portal"] });
  };
  const s = state.data;
  if (!s) return null;
  const e = s.current;
  return (
    <div className="rounded border border-gray-200 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-navy-900">
          E-way bill {e ? <Badge tone={e.expired ? "red" : "green"}>{e.expired ? "Expired" : e.number}</Badge> : s.needed ? <Badge tone="amber">Needed</Badge> : <Badge>Not needed</Badge>}
        </p>
        <div className="flex gap-2">
          {s.can_generate ? <button type="button" className={s.needed ? "btn-primary" : "btn-secondary"} onClick={() => setMaking(true)}>Make e-way bill</button> : null}
          {e?.can_update_vehicle ? <button type="button" className="btn-secondary" onClick={() => setChanging(true)}>{e.vehicle_number ? "Change vehicle" : "Add vehicle"}</button> : null}
          {e?.can_cancel ? <button type="button" className="btn-secondary" onClick={() => setCancelling(true)}>Cancel</button> : null}
        </div>
      </div>
      {!e ? <p className="mt-1 text-xs text-gray-500">{s.why} Value with GST {rupees(s.value)}.</p> : (
        <div className="mt-2 space-y-1">
          <p><span className="font-mono">{e.number}</span> · {when(e.generated_at)} · {e.distance_km} km by {e.transport_mode_label.toLowerCase()}</p>
          <p>{e.vehicle_number ? <>Vehicle <span className="font-mono">{e.vehicle_number}</span></> : "Vehicle not added yet (Part B)"}{e.transporter_name ? ` · ${e.transporter_name}` : ""}</p>
          <p className={e.expired ? "text-red-700" : "text-gray-600"}>{e.valid_until ? `Valid until ${when(e.valid_until)}` : "Validity starts when the vehicle is added."}</p>
          {e.vehicles.length > 1 ? <p className="text-xs text-gray-500">Vehicles: {e.vehicles.map((v) => `${v.vehicle_number} (${v.reason_label.toLowerCase()}${v.from_place ? `, ${v.from_place}` : ""})`).join(" → ")}</p> : null}
          {e.provider === "mock" ? <p className="text-xs text-amber-700">Test portal: not a valid e-way bill until a GSP is connected.</p> : null}
        </div>
      )}
      {s.history.map((h) => <p key={h.id} className="mt-1 text-xs text-gray-500">Cancelled {h.number} ({h.cancel_reason_label}: {h.cancel_remark})</p>)}
      {making ? <MakeEwb defaultVehicle={vehicle} onClose={() => setMaking(false)} send={async (body) => { done(await ewbCall(kind, id, "generate", body)); setMaking(false); }} /> : null}
      {changing && e ? <ChangeVehicle first={!e.vehicle_number} onClose={() => setChanging(false)} send={async (body) => { done(await ewbCall(kind, id, "vehicle", body)); setChanging(false); }} /> : null}
      {cancelling ? (
        <CancelModal title="Cancel the e-way bill" reasons={EWB_REASONS} note="Only within 24 hours of making it, and before the goods are checked on the way."
          onClose={() => setCancelling(false)} send={async (reason, remark) => { done(await ewbCall(kind, id, "cancel", { reason, remark })); setCancelling(false); }} />
      ) : null}
    </div>
  );
}

function MakeEwb({ defaultVehicle, onClose, send }: { defaultVehicle: string; onClose: () => void; send: (body: Schemas["EwbIn"]) => Promise<void> }) {
  const [form, setForm] = useState({ distance_km: "", transport_mode: "1", vehicle_type: "R", vehicle_number: defaultVehicle, transporter_id: "",
    transporter_name: "", transport_document: "", transport_document_date: "" });
  const set = (f: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [f]: e.target.value });
  const run = useMutation({ mutationFn: () => send({ ...form, distance_km: Number(form.distance_km), transport_document_date: form.transport_document_date || null }) });
  const road = form.transport_mode === "1";
  return (
    <Modal title="Make an e-way bill" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!form.distance_km || run.isPending} onClick={() => run.mutate()}>Make it</button>
      </>
    }>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Distance by road (km)" hint="A day's validity for every 200 km.">{(id) => <input id={id} className="field-input" inputMode="numeric" value={form.distance_km} onChange={set("distance_km")} />}</Field>
        <Field label="Mode">
          {(id) => <select id={id} className="field-input" value={form.transport_mode} onChange={set("transport_mode")}><option value="1">Road</option><option value="2">Rail</option><option value="3">Air</option><option value="4">Ship</option></select>}
        </Field>
        {road ? <Field label="Vehicle number" hint="Leave blank if the transporter will add it.">{(id) => <input id={id} className="field-input uppercase" value={form.vehicle_number} onChange={set("vehicle_number")} />}</Field>
          : <Field label="RR / airway bill / B/L no.">{(id) => <input id={id} className="field-input" maxLength={15} value={form.transport_document} onChange={set("transport_document")} />}</Field>}
        {road ? (
          <Field label="Vehicle type">{(id) => <select id={id} className="field-input" value={form.vehicle_type} onChange={set("vehicle_type")}><option value="R">Regular</option><option value="O">Over-dimensional cargo</option></select>}</Field>
        ) : <Field label="Its date">{(id) => <input id={id} type="date" className="field-input" value={form.transport_document_date} onChange={set("transport_document_date")} />}</Field>}
        <Field label="Transporter ID" hint="Their GSTIN or TRANSIN (optional).">{(id) => <input id={id} className="field-input uppercase" maxLength={15} value={form.transporter_id} onChange={set("transporter_id")} />}</Field>
        <Field label="Transporter name">{(id) => <input id={id} className="field-input" maxLength={100} value={form.transporter_name} onChange={set("transporter_name")} />}</Field>
      </div>
      <ErrorText error={run.error} />
    </Modal>
  );
}

function ChangeVehicle({ first, onClose, send }: { first: boolean; onClose: () => void; send: (body: Schemas["EwbVehicleIn"]) => Promise<void> }) {
  const [vehicle, setVehicle] = useState("");
  const [place, setPlace] = useState("");
  const [reason, setReason] = useState("1");
  const run = useMutation({ mutationFn: () => send({ vehicle_number: vehicle, from_place: place, reason: first ? "" : reason, remark: "" }) });
  return (
    <Modal title={first ? "Add the vehicle (Part B)" : "Change the vehicle"} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!vehicle.trim() || run.isPending} onClick={() => run.mutate()}>Save</button>
      </>
    }>
      <Field label="Vehicle number">{(id) => <input id={id} className="field-input uppercase" value={vehicle} onChange={(e) => setVehicle(e.target.value)} />}</Field>
      {!first ? (
        <>
          <Field label="Why">{(id) => <select id={id} className="field-input" value={reason} onChange={(e) => setReason(e.target.value)}><option value="1">Breakdown</option><option value="2">Transshipment</option><option value="3">Other</option></select>}</Field>
          <Field label="Where the goods are now">{(id) => <input id={id} className="field-input" maxLength={50} value={place} onChange={(e) => setPlace(e.target.value)} />}</Field>
        </>
      ) : null}
      <ErrorText error={run.error} />
    </Modal>
  );
}

/** Accounts > GST portal: every IRN and e-way bill, and what still needs one. */
export function GstPortalPage() {
  const register = useQuery({ queryKey: ["gst-portal"], queryFn: async () => unwrap(await api.GET("/api/v1/gst-portal/register")) });
  const r = register.data;
  const KIND: Record<string, string> = { invoice: "Invoice", note: "Note", dispatch: "Delivery challan", job_work: "Job work challan" };
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-navy-900">E-invoices and e-way bills</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-600">
          IRNs for B2B invoices and notes (once turnover has crossed ₹5 crore; turn it on under Settings, Company) and e-way bills for goods worth over ₹50,000 or sent for inter-state job work. Open the document to register it.
        </p>
      </div>
      {register.isLoading ? null : register.error || !r ? <ErrorText error={register.error} /> : (
        <>
          {r.provider === "mock" ? <Notice tone="amber">Connected to the MakeSetu test portal. Numbers are realistic but not valid for GST until a GST Suvidha Provider is set up.</Notice> : null}
          {r.pending.length ? (
            <div className="card">
              <p className="mb-2 font-semibold text-navy-900">Still to do (last 30 days)</p>
              <ul className="divide-y divide-gray-100 text-sm">
                {r.pending.map((p) => (
                  <li key={`${p.document_kind}-${p.document_id}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span>{KIND[p.document_kind]} <span className="font-semibold">{p.document_number}</span> · {p.party} · {day(p.date)}</span>
                    <span className="flex items-center gap-2">{rupees(p.value)} <Badge tone="amber">{p.why}</Badge></span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="card overflow-x-auto p-0">
            {r.rows.length === 0 ? <p className="p-5 text-sm text-gray-500">Nothing registered yet.</p> : (
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">Document</th>
                    <th scope="col" className="table-head">IRN / e-way bill</th>
                    <th scope="col" className="table-head">When</th>
                    <th scope="col" className="table-head text-right">Value</th>
                    <th scope="col" className="table-head">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {r.rows.map((row) => (
                    <tr key={`${row.kind}-${row.id}`}>
                      <td className="table-cell text-sm">{KIND[row.document_kind]} <span className="font-semibold">{row.document_number}</span><p className="text-xs text-gray-500">{row.party}</p></td>
                      <td className="table-cell font-mono text-xs">{row.kind === "einvoice" ? `IRN ${row.number.slice(0, 16)}…` : `EWB ${row.number}`}</td>
                      <td className="table-cell text-sm">{when(row.date)}{row.valid_until ? <p className="text-xs text-gray-500">valid to {when(row.valid_until)}</p> : null}</td>
                      <td className="table-cell text-right text-sm">{rupees(row.value)}</td>
                      <td className="table-cell"><Badge tone={row.status === "active" ? "green" : "red"}>{row.status_label}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
