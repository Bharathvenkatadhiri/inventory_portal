import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { useWarehouses } from "../inventory/InventoryLayout";
import { ReasonModal, StatusBadge, todayIso } from "../purchase/PurchaseLayout";
import { BomTree } from "./BomTree";
import { CostSheetTable } from "./CostingPage";

type WorkOrder = Schemas["WorkOrderOut"];
const TONE: Record<string, string> = { draft: "draft", released: "issued", in_progress: "partially_received", completed: "received", closed: "closed", cancelled: "cancelled" };

/** Planned production runs, with sub-assembly orders, progress and planned-vs-actual. */
export function WorkOrdersPage() {
  const can = useCan();
  const writable = useWritable();
  const [status, setStatus] = useState("open");
  const orders = useQuery({
    queryKey: ["work-orders", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/work-orders", { params: { query: { status } } })),
  });
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Work orders"
        description="Plan what to make, how many and by when. Releasing an order can also raise orders for the sub-assemblies it's short of. Production recorded against an order uses its materials and counts towards it."
        actions={can("work_order.manage") && writable ? <button type="button" className="btn-primary" onClick={() => setCreating(true)}>New work order</button> : null}
      />
      <select className="field-input max-w-[220px]" aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="open">Open</option>
        <option value="completed">Completed</option>
        <option value="closed">Closed short</option>
        <option value="all">All</option>
      </select>
      {orders.isLoading ? <Loading /> : orders.error ? <ErrorText error={orders.error} /> : (orders.data ?? []).length === 0 ? (
        <Empty title={status === "open" ? "No open work orders" : "None"} />
      ) : (
        <div className="space-y-3">
          {(orders.data ?? []).map((wo) => {
            const pct = Math.min(100, (Number(wo.made) / Number(wo.quantity)) * 100);
            return (
              <Card key={wo.id}>
                <button type="button" className="w-full text-left" onClick={() => setOpenId(wo.id)}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-navy-900">{wo.number} · {wo.item_name}</p>
                      <p className="text-sm text-gray-500">
                        {quantity(wo.quantity, wo.unit)} by {day(wo.due_date)}{wo.parent ? ` · for ${wo.parent.number}` : ""}{wo.children.length ? ` · ${wo.children.length} sub-assembly order${wo.children.length === 1 ? "" : "s"}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {wo.overdue ? <Badge tone="red">Overdue</Badge> : null}
                      <StatusBadge status={TONE[wo.status] ?? wo.status} label={wo.status_label} />
                    </div>
                  </div>
                  <div className="mt-3 h-2 rounded-full bg-gray-100" aria-hidden="true"><div className="h-2 rounded-full bg-accent-500" style={{ width: `${pct}%` }} /></div>
                  <p className="mt-1 text-xs text-gray-500">{quantity(wo.made)} of {quantity(wo.quantity, wo.unit)} made{Number(wo.rejected) ? ` · ${quantity(wo.rejected)} rejected` : ""}</p>
                </button>
              </Card>
            );
          })}
        </div>
      )}
      {creating ? <WorkOrderForm onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setOpenId(id); }} /> : null}
      {openId ? <WorkOrderDetail id={openId} onOpen={setOpenId} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function refresh(queryClient: ReturnType<typeof useQueryClient>) {
  for (const key of ["work-orders", "work-order", "production-entries", "stock", "items", "qc-lots"]) queryClient.invalidateQueries({ queryKey: [key] });
}

function WorkOrderForm({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const queryClient = useQueryClient();
  const boms = useQuery({ queryKey: ["boms", "active"], queryFn: async () => unwrap(await api.GET("/api/v1/boms", { params: { query: { active: true } } })) });
  const warehouses = useWarehouses();
  const stores = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [bomId, setBomId] = useState<number | null>(null);
  const [qty, setQty] = useState("");
  const [due, setDue] = useState("");
  const [fromId, setFromId] = useState<number | null>(null);
  const [toId, setToId] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  const bom = (boms.data ?? []).find((b) => b.id === bomId);
  const source = fromId ?? stores.find((w) => w.is_default)?.id ?? stores[0]?.id ?? null;
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/work-orders", {
      body: { item_id: bom!.item_id, bom_id: bom!.id, quantity: qty, due_date: due || null, source_warehouse_id: source!, target_warehouse_id: toId ?? source, notes },
    })),
    onSuccess: (wo) => { refresh(queryClient); onCreated(wo.id); },
  });
  return (
    <Modal wide title="New work order" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!bom || !(Number(qty) > 0) || save.isPending} onClick={() => save.mutate()}>Save draft</button>
      </>
    }>
      {boms.isLoading ? <Loading /> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Make">
              {(id) => (
                <select id={id} className="field-input" value={bomId ?? ""} onChange={(e) => setBomId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Choose</option>
                  {(boms.data ?? []).map((b) => <option key={b.id} value={b.id}>{b.item_name} ({b.item_code}){b.is_default ? "" : ` · BOM ${b.name}`}</option>)}
                </select>
              )}
            </Field>
            <Field label={`Quantity${bom ? ` (${bom.unit})` : ""}`}>{(id) => <input id={id} className="field-input" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />}</Field>
            <Field label="Materials from">
              {(id) => (
                <select id={id} className="field-input" value={source ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
                  {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Finished goods to">
              {(id) => (
                <select id={id} className="field-input" value={toId ?? source ?? ""} onChange={(e) => setToId(Number(e.target.value))}>
                  {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Due by">{(id) => <input id={id} type="date" className="field-input" value={due} min={todayIso()} onChange={(e) => setDue(e.target.value)} />}</Field>
            <Field label="Notes">{(id) => <input id={id} className="field-input" maxLength={300} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
          </div>
          {bom && Number(qty) > 0 && source ? (
            <div>
              <p className="field-label">What it needs (all levels)</p>
              <BomTree bomId={bom.id} quantity={qty} warehouseId={source} />
            </div>
          ) : null}
        </>
      )}
      <ErrorText error={save.error} />
    </Modal>
  );
}

function WorkOrderDetail({ id, onOpen, onClose }: { id: number; onOpen: (id: number) => void; onClose: () => void }) {
  const queryClient = useQueryClient();
  const order = useQuery({ queryKey: ["work-order", id], queryFn: async () => unwrap(await api.GET("/api/v1/work-orders/{wo_id}", { params: { path: { wo_id: id } } })) });
  const [subassemblies, setSubassemblies] = useState(true);
  const [asking, setAsking] = useState<"close" | "cancel" | null>(null);
  const [recording, setRecording] = useState(false);
  const release = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/work-orders/{wo_id}/release", { params: { path: { wo_id: id } }, body: { make_subassemblies: subassemblies } })),
    onSuccess: () => refresh(queryClient),
  });
  const wo = order.data;
  return (
    <Modal wide title={wo ? `${wo.number} · ${wo.item_name}` : "Work order"} onClose={onClose} footer={
      wo ? (
        <>
          {wo.actions.cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => setAsking("cancel")}>Cancel order</button> : null}
          {wo.actions.close ? <button type="button" className="btn-secondary mr-auto" onClick={() => setAsking("close")}>Close short</button> : null}
          {wo.actions.release ? <button type="button" className="btn-primary" disabled={release.isPending} onClick={() => release.mutate()}>Release</button> : null}
          {wo.actions.record ? <button type="button" className="btn-primary" onClick={() => setRecording(true)}>Record production</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
        </>
      ) : null
    }>
      {order.isLoading || !wo ? <Loading /> : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <StatusBadge status={TONE[wo.status] ?? wo.status} label={wo.status_label} />
            <span>{quantity(wo.quantity, wo.unit)} by {day(wo.due_date)} · BOM {wo.bom_name} · materials from {wo.source_warehouse} · to {wo.target_warehouse}</span>
            {wo.overdue ? <Badge tone="red">Overdue</Badge> : null}
          </div>
          {wo.close_reason ? <Notice tone="amber">{wo.status_label}: {wo.close_reason}</Notice> : null}
          {wo.parent ? <p className="text-sm">Sub-assembly for <button type="button" className="btn-link" onClick={() => onOpen(wo.parent!.id)}>{wo.parent.number}</button> ({wo.parent.item_name}).</p> : null}
          {wo.actions.release ? (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={subassemblies} onChange={(e) => setSubassemblies(e.target.checked)} />
              Also raise work orders for sub-assemblies we're short of (all levels)
            </label>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="card"><p className="text-xs text-gray-500">Made</p><p className="text-lg font-bold">{quantity(wo.made, wo.unit)}</p></div>
            <div className="card"><p className="text-xs text-gray-500">Still to make</p><p className="text-lg font-bold">{quantity(wo.remaining)}</p></div>
            <div className="card"><p className="text-xs text-gray-500">Rejected</p><p className="text-lg font-bold">{quantity(wo.rejected)}</p></div>
            <div className="card"><p className="text-xs text-gray-500">Cost a unit · plan / actual</p><p className="text-lg font-bold">{rupees(wo.planned_unit_cost)} / {Number(wo.made) ? rupees(wo.actual_unit_cost) : "—"}</p></div>
          </div>
          <table className="w-full">
            <thead className="border-b border-gray-100"><tr><th scope="col" className="table-head">Material</th><th scope="col" className="table-head text-right">Planned</th><th scope="col" className="table-head text-right">Used so far</th><th scope="col" className="table-head text-right">In {wo.source_warehouse}</th></tr></thead>
            <tbody className="divide-y divide-gray-100">
              {wo.materials.map((m) => (
                <tr key={m.item_code}>
                  <td className="table-cell text-sm">{m.item_name} <span className="text-gray-500">{m.item_code}</span></td>
                  <td className="table-cell text-right text-sm">{quantity(m.planned, m.unit)}</td>
                  <td className={`table-cell text-right text-sm ${Number(m.used) > Number(m.planned) ? "font-semibold text-amber-700" : ""}`}>{quantity(m.used)}</td>
                  <td className={`table-cell text-right text-sm ${Number(m.short) > 0 ? "font-semibold text-red-700" : ""}`}>{quantity(m.on_hand)}{Number(m.short) > 0 ? ` (short ${quantity(m.short)})` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Number(wo.made) > 0 ? <WorkOrderCosts id={wo.id} /> : null}
          {wo.children.length ? (
            <div>
              <p className="field-label">Sub-assembly orders</p>
              <ul className="divide-y divide-gray-100 text-sm">
                {wo.children.map((c) => (
                  <li key={c.id} className="flex items-center justify-between py-1">
                    <button type="button" className="btn-link" onClick={() => onOpen(c.id)}>{c.number} · {quantity(c.quantity)} {c.item_name}</button>
                    <StatusBadge status={TONE[c.status] ?? c.status} label={c.status_label} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <ErrorText error={release.error} />
          {recording ? <RecordAgainst wo={wo} onClose={() => setRecording(false)} /> : null}
          {asking ? (
            <ReasonModal title={asking === "close" ? "Close short" : "Cancel this work order"}
              label={asking === "close" ? "Why won't the rest be made?" : "Why? Unstarted sub-assembly orders are cancelled too."}
              button={asking === "close" ? "Close short" : "Cancel order"} onClose={() => setAsking(null)}
              send={async (reason) => {
                const path = { params: { path: { wo_id: wo.id } }, body: { reason } };
                if (asking === "close") unwrap(await api.POST("/api/v1/work-orders/{wo_id}/close", path));
                else unwrap(await api.POST("/api/v1/work-orders/{wo_id}/cancel", path));
                refresh(queryClient);
                setAsking(null);
              }} />
          ) : null}
        </>
      )}
    </Modal>
  );
}

function RecordAgainst({ wo, onClose }: { wo: WorkOrder; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [good, setGood] = useState(String(Number(wo.remaining)));
  const [rejected, setRejected] = useState("");
  const [shift, setShift] = useState("");
  const [on, setOn] = useState(todayIso());
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/production/entries", {
      body: { work_order_id: wo.id, quantity_produced: good || "0", quantity_rejected: rejected || "0", entry_date: on, shift, notes: "", consumption: [], output_batches: [] },
    })),
    onSuccess: () => { refresh(queryClient); onClose(); },
  });
  return (
    <Modal title={`Record production on ${wo.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Record</button>
      </>
    }>
      <p className="text-sm text-gray-600">{quantity(wo.remaining, wo.unit)} {wo.item_name} still to make. Materials come from {wo.source_warehouse} at the BOM's quantities.</p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Field label="Good">{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={good} onChange={(e) => setGood(e.target.value)} />}</Field>
        <Field label="Rejected">{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={rejected} onChange={(e) => setRejected(e.target.value)} />}</Field>
        <Field label="Date">{(id) => <input id={id} type="date" className="field-input" value={on} max={todayIso()} onChange={(e) => setOn(e.target.value)} />}</Field>
        <Field label="Shift">{(id) => <input id={id} className="field-input" maxLength={20} value={shift} onChange={(e) => setShift(e.target.value)} />}</Field>
      </div>
      <ErrorText error={save.error} />
    </Modal>
  );
}

/** Standard against actual for what's been made, for people who see production costs. */
function WorkOrderCosts({ id }: { id: number }) {
  const can = useCan();
  const sheet = useQuery({
    queryKey: ["work-orders", id, "costs"],
    enabled: can("costing.manage"),
    queryFn: async () => unwrap(await api.GET("/api/v1/work-orders/{wo_id}/costs", { params: { path: { wo_id: id } } })),
  });
  if (!can("costing.manage") || !sheet.data) return null;
  return (
    <div>
      <p className="field-label">
        Cost so far{sheet.data.yield_percent != null ? ` · ${Number(sheet.data.yield_percent)}% yield` : ""}
        {Number(sheet.data.labour_hours) || Number(sheet.data.machine_hours) ? ` · ${Number(sheet.data.labour_hours)} labour h, ${Number(sheet.data.machine_hours)} machine h` : ""}
      </p>
      <CostSheetTable sheet={sheet.data} />
    </div>
  );
}
