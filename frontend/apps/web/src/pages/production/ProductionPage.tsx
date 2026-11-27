import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useActive, useCan, useWritable } from "../../lib/session";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { BatchesIn, batchesIn, BatchPicker, batchText, picksIn, useLineBatches, useTracking } from "../inventory/batches";
import { ReasonModal, StatusBadge, todayIso } from "../purchase/PurchaseLayout";

type Entry = Schemas["EntryOut"];

/** Production entries: what was made, what it used. Simple enough for the shop floor on a phone. */
export function ProductionPage() {
  const can = useCan();
  const writable = useWritable();
  const entries = useQuery({ queryKey: ["production-entries"], queryFn: async () => unwrap(await api.GET("/api/v1/production/entries")) });
  const [recording, setRecording] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const open = (entries.data ?? []).find((e) => e.id === openId);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Production"
        description="Record what was made. Its components come out of stock and the good units go in, at the cost of what was used."
        actions={can("production.record") && writable ? <button type="button" className="btn-primary" onClick={() => setRecording(true)}>Record production</button> : null}
      />
      <Card className="overflow-x-auto p-0">
        {entries.isLoading ? <Loading /> : entries.error ? <div className="p-5"><ErrorText error={entries.error} /></div> : (entries.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="Nothing recorded yet" /></div>
        ) : (
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Entry</th>
                <th scope="col" className="table-head">Made</th>
                <th scope="col" className="table-head text-right">Good · rejected</th>
                <th scope="col" className="table-head text-right">Cost a unit</th>
                <th scope="col" className="table-head">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(entries.data ?? []).map((e) => (
                <tr key={e.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setOpenId(e.id)}>
                  <td className="table-cell"><button type="button" className="font-semibold text-navy-900 hover:underline">{e.number}</button><p className="text-xs text-gray-500">{day(e.entry_date)}{e.shift ? ` · shift ${e.shift}` : ""} · {e.recorded_by}</p></td>
                  <td className="table-cell text-sm">{e.item_name}<p className="text-xs text-gray-500">{e.item_code} · BOM {e.bom_name}</p></td>
                  <td className="table-cell text-right text-sm">{quantity(e.quantity_produced, e.unit)}{Number(e.quantity_rejected) ? <span className="text-red-700"> · {quantity(e.quantity_rejected)}</span> : null}</td>
                  <td className="table-cell text-right text-sm">{rupees(e.unit_cost)}</td>
                  <td className="table-cell"><StatusBadge status={e.status} label={e.status_label} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {recording ? <RecordForm onClose={() => setRecording(false)} /> : null}
      {open ? <EntryDetail entry={open} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function RecordForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const boms = useQuery({ queryKey: ["boms", "active"], queryFn: async () => unwrap(await api.GET("/api/v1/boms", { params: { query: { active: true } } })) });
  const warehouses = useWarehouses();
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [bomId, setBomId] = useState<number | null>(null);
  const [good, setGood] = useState("");
  const [rejected, setRejected] = useState("");
  const [fromId, setFromId] = useState<number | null>(null);
  const [toId, setToId] = useState<number | null>(null);
  const [made, setMade] = useState(todayIso());
  const [shift, setShift] = useState("");
  const [notes, setNotes] = useState("");
  const [used, setUsed] = useState<Record<number, string>>({});
  const tracking = useTracking();
  const lineBatches = useLineBatches<number>();
  const costed = useActive().features.includes("production_costing");
  const machines = useQuery({ queryKey: ["machines"], enabled: costed, queryFn: async () => unwrap(await api.GET("/api/v1/machines")) });
  const [labourHours, setLabourHours] = useState("");
  const [machineId, setMachineId] = useState<number | null>(null);
  const [machineHours, setMachineHours] = useState("");
  const [scrap, setScrap] = useState<{ item_id: number; quantity: string; rate: string }[] | null>(null);
  const items = useItems();
  const scrapItems = (items.data ?? []).filter((i) => i.is_active && i.tracks_stock && i.kind !== "finished_good")
    .sort((x, y) => Number(y.kind === "scrap") - Number(x.kind === "scrap"));
  const defaultWarehouse = activeWarehouses.find((w) => w.is_default)?.id ?? activeWarehouses[0]?.id ?? null;
  const source = fromId ?? defaultWarehouse;
  const total = (Number(good) || 0) + (Number(rejected) || 0);
  const chosenBom = (boms.data ?? []).find((b) => b.id === bomId) ?? (boms.data ?? []).find((b) => b.is_default) ?? null;
  const bom = bomId ? chosenBom : null;
  const plan = useQuery({
    queryKey: ["production-plan", bom?.id, total, source],
    enabled: !!bom && total > 0 && source !== null,
    queryFn: async () => unwrap(await api.GET("/api/v1/production/plan", { params: { query: { bom_id: bom!.id, quantity: String(total), warehouse_id: source! } } })),
  });
  const short = (plan.data ?? []).some((l) => Number(used[l.item_id] ?? l.planned) > Number(l.on_hand));
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/production/entries", {
      body: {
        bom_id: bom!.id, quantity_produced: good || "0", quantity_rejected: rejected || "0", entry_date: made,
        source_warehouse_id: source!, target_warehouse_id: toId ?? source!, shift, notes,
        consumption: (plan.data ?? [])
          .map((l) => ({ item_id: l.item_id, quantity: used[l.item_id] || null, batches: picksIn(lineBatches.picks(l.item_id)) }))
          .filter((c) => c.quantity !== null || c.batches.length > 0),
        output_batches: batchesIn(tracking(bom!.item_id), lineBatches.rows(0), lineBatches.serials(0)),
        ...(costed ? {
          labour_hours: labourHours.trim() || null, machine_hours: machineHours.trim() || null, machine_id: machineId,
          scrap: scrap === null ? null : scrap.filter((r) => r.quantity.trim()).map((r) => ({ item_id: r.item_id, quantity: r.quantity, rate: r.rate.trim() || null })),
        } : {}),
      },
    })),
    onSuccess: () => {
      for (const key of ["production-entries", "stock", "items"]) queryClient.invalidateQueries({ queryKey: [key] });
      onClose();
    },
  });

  return (
    <Modal wide title="Record production" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!bom || total <= 0 || short || save.isPending} onClick={() => save.mutate()}>Record</button>
      </>
    }>
      {boms.isLoading ? <Loading /> : (boms.data ?? []).length === 0 ? (
        <Notice tone="amber">There are no bills of materials yet. Someone who manages BOMs needs to add one first.</Notice>
      ) : (
        <>
          <Field label="What was made">
            {(id) => (
              <select id={id} className="field-input" value={bomId ?? ""} onChange={(e) => { setBomId(e.target.value ? Number(e.target.value) : null); setUsed({}); }}>
                <option value="">Choose</option>
                {(boms.data ?? []).map((b) => <option key={b.id} value={b.id}>{b.item_name} ({b.item_code}){b.is_default ? "" : ` · BOM ${b.name}`}</option>)}
              </select>
            )}
          </Field>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Field label={`Good${bom ? ` (${bom.unit})` : ""}`}>{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={good} onChange={(e) => setGood(e.target.value)} />}</Field>
            <Field label="Rejected">{(id) => <input id={id} className="field-input text-lg" inputMode="decimal" value={rejected} onChange={(e) => setRejected(e.target.value)} />}</Field>
            <Field label="Date">{(id) => <input id={id} type="date" className="field-input" value={made} max={todayIso()} onChange={(e) => setMade(e.target.value)} />}</Field>
            <Field label="Shift">{(id) => <input id={id} className="field-input" value={shift} maxLength={20} placeholder="e.g. A" onChange={(e) => setShift(e.target.value)} />}</Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Materials from">
              {(id) => (
                <select id={id} className="field-input" value={source ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
                  {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Finished goods to">
              {(id) => (
                <select id={id} className="field-input" value={toId ?? source ?? ""} onChange={(e) => setToId(Number(e.target.value))}>
                  {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
          </div>
          {bom && Number(good) > 0 && tracking(bom.item_id) !== "none" ? (
            <BatchesIn label={bom.item_name} tracking={tracking(bom.item_id)} rows={lineBatches.rows(0)} onRows={(r) => lineBatches.setRows(0, r)}
              serials={lineBatches.serials(0)} onSerials={(t) => lineBatches.setSerials(0, t)} />
          ) : null}
          {bom && total > 0 ? (
            plan.isLoading ? <Loading /> : (
              <table className="w-full">
                <thead className="border-b border-gray-100">
                  <tr><th scope="col" className="table-head">Material</th><th scope="col" className="table-head text-right">BOM says</th><th scope="col" className="table-head text-right">In stock</th><th scope="col" className="table-head w-36">Actually used</th></tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(plan.data ?? []).map((l) => {
                    const value = used[l.item_id] ?? "";
                    const over = Number(value === "" ? l.planned : value) > Number(l.on_hand);
                    return (
                      <tr key={l.item_id}>
                        <td className="table-cell text-sm">
                          {l.item_name} <span className="text-gray-500">{l.item_code}</span>
                          <BatchPicker label={l.item_name} tracking={tracking(l.item_id)} itemId={l.item_id} warehouseId={source}
                            picks={lineBatches.picks(l.item_id)} onPicks={(p) => lineBatches.setPicks(l.item_id, p)} />
                        </td>
                        <td className="table-cell text-right text-sm">{quantity(l.planned, l.unit)}</td>
                        <td className={`table-cell text-right text-sm ${over ? "font-semibold text-red-700" : ""}`}>{quantity(l.on_hand, l.unit)}</td>
                        <td className="table-cell"><input className="field-input" inputMode="decimal" placeholder={String(Number(l.planned))} aria-label={`Actually used, ${l.item_name}`} value={value} onChange={(e) => setUsed({ ...used, [l.item_id]: e.target.value })} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )
          ) : null}
          {costed && bom && total > 0 ? (
            <fieldset className="rounded border border-gray-200 p-3">
              <legend className="px-1 text-sm font-semibold text-gray-700">Time and scrap</legend>
              <p className="mb-2 text-xs text-gray-500">Leave blank to use the BOM's standard for {total} made.</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Labour hours">
                  {(id) => <input id={id} className="field-input" inputMode="decimal" value={labourHours} placeholder={String(+(Number(bom.labour_hours) * total / Number(bom.output_quantity)).toFixed(2))} onChange={(e) => setLabourHours(e.target.value)} />}
                </Field>
                <Field label="Machine">
                  {(id) => (
                    <select id={id} className="field-input" value={machineId ?? bom.machine_id ?? ""} onChange={(e) => setMachineId(e.target.value ? Number(e.target.value) : null)}>
                      <option value="">None</option>
                      {(machines.data ?? []).filter((m) => m.is_active).map((m) => <option key={m.id} value={m.id}>{m.code} · {m.name}</option>)}
                    </select>
                  )}
                </Field>
                <Field label="Machine hours">
                  {(id) => <input id={id} className="field-input" inputMode="decimal" value={machineHours} placeholder={String(+(Number(bom.machine_hours) * total / Number(bom.output_quantity)).toFixed(2))} onChange={(e) => setMachineHours(e.target.value)} />}
                </Field>
              </div>
              {scrap === null ? (
                <p className="mt-2 text-sm text-gray-600">
                  {bom.scrap_item_id ? `Scrap: about ${+(Number(bom.scrap_quantity) * total / Number(bom.output_quantity)).toFixed(3)} ${bom.scrap_item_name} (from the BOM). ` : "No scrap expected. "}
                  <button type="button" className="btn-link" onClick={() => setScrap(bom.scrap_item_id ? [{ item_id: bom.scrap_item_id, quantity: "", rate: "" }] : [])}>Enter actual scrap</button>
                </p>
              ) : (
                <div className="mt-2 space-y-1">
                  {scrap.map((r, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2">
                      <select className="field-input w-56 text-sm" aria-label={`Scrap item ${i + 1}`} value={r.item_id}
                        onChange={(e) => setScrap(scrap.map((x, j) => (j === i ? { ...x, item_id: Number(e.target.value) } : x)))}>
                        {scrapItems.map((it) => <option key={it.id} value={it.id}>{it.code} · {it.name}</option>)}
                      </select>
                      <input className="field-input w-24 text-sm" inputMode="decimal" placeholder="Qty" aria-label={`Scrap quantity ${i + 1}`} value={r.quantity}
                        onChange={(e) => setScrap(scrap.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
                      <input className="field-input w-28 text-sm" inputMode="decimal" placeholder="₹ a unit" aria-label={`Scrap rate ${i + 1}`} value={r.rate}
                        onChange={(e) => setScrap(scrap.map((x, j) => (j === i ? { ...x, rate: e.target.value } : x)))} />
                      <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove scrap ${i + 1}`} onClick={() => setScrap(scrap.filter((_, j) => j !== i))}>✕</button>
                    </div>
                  ))}
                  <div className="flex gap-3 text-sm">
                    {scrapItems.length ? <button type="button" className="btn-link" onClick={() => setScrap([...scrap, { item_id: scrapItems[0].id, quantity: "", rate: "" }])}>+ Add scrap</button> : null}
                    <button type="button" className="btn-link" onClick={() => setScrap(null)}>Use the BOM's instead</button>
                  </div>
                  {scrap.length === 0 ? <p className="text-xs text-gray-500">No scrap recorded.</p> : null}
                  <p className="text-xs text-gray-500">Rate left blank: the item's sale price.</p>
                </div>
              )}
            </fieldset>
          ) : null}
          {short ? <Notice tone="red">Not enough material in stock for this. Receive or move stock first, or adjust what was used.</Notice> : null}
          <Field label="Notes">{(id) => <input id={id} className="field-input" value={notes} maxLength={300} onChange={(e) => setNotes(e.target.value)} />}</Field>
        </>
      )}
      <ErrorText error={save.error} />
    </Modal>
  );
}

function EntryDetail({ entry: e, onClose }: { entry: Entry; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  return (
    <Modal title={`${e.number} · ${e.item_name}`} onClose={onClose} footer={
      <>
        {e.can_cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => setCancelling(true)}>Cancel entry</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={e.status} label={e.status_label} />
        <span>{day(e.entry_date)}{e.shift ? ` · shift ${e.shift}` : ""} · recorded by {e.recorded_by}</span>
      </div>
      {e.status === "cancelled" ? <Notice tone="red">Cancelled: {e.cancel_reason}</Notice> : null}
      <p className="text-sm">
        <strong>{quantity(e.quantity_produced, e.unit)}</strong> good{Number(e.quantity_rejected) ? `, ${quantity(e.quantity_rejected, e.unit)} rejected` : ""} into {e.target_warehouse}.
        {Number(e.total_cost) !== Number(e.material_cost) ? "Cost" : "Materials"} {rupees(e.total_cost)} → <strong>{rupees(e.unit_cost)}</strong> a good unit.
      </p>
      {Number(e.labour_cost) || Number(e.machine_cost) || Number(e.overhead_cost) || Number(e.scrap_value) ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
          <div><dt className="text-gray-500">Materials</dt><dd>{rupees(e.material_cost)}</dd></div>
          <div><dt className="text-gray-500">Labour{Number(e.labour_hours) ? ` (${Number(e.labour_hours)} h)` : ""}</dt><dd>{rupees(e.labour_cost)}</dd></div>
          <div><dt className="text-gray-500">Machine{e.machine_name ? ` (${Number(e.machine_hours)} h, ${e.machine_name})` : ""}</dt><dd>{rupees(e.machine_cost)}</dd></div>
          <div><dt className="text-gray-500">Overheads</dt><dd>{rupees(e.overhead_cost)}</dd></div>
          <div><dt className="text-gray-500">Less scrap</dt><dd>{rupees(e.scrap_value)}</dd></div>
          <div><dt className="text-gray-500">Total</dt><dd className="font-semibold">{rupees(e.total_cost)}</dd></div>
        </dl>
      ) : null}
      {e.scrap.length ? <p className="text-sm text-gray-600">Scrap into stock: {e.scrap.map((s) => `${quantity(s.quantity, s.unit)} ${s.item_name} at ${rupees(s.rate)}`).join(", ")}</p> : null}
      {e.output_batches.length ? <p className="text-sm text-gray-600">Made as: <span className="font-mono">{batchText(e.output_batches)}</span></p> : null}
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Material (from {e.source_warehouse})</th><th scope="col" className="table-head text-right">BOM</th><th scope="col" className="table-head text-right">Used</th><th scope="col" className="table-head text-right">At</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {e.consumption.map((c) => (
            <tr key={c.item_code}>
              <td className="table-cell text-sm">
                {c.item_name} <span className="text-gray-500">{c.item_code}</span>
                {c.batches.length ? <p className="font-mono text-xs text-gray-500">{batchText(c.batches)}</p> : null}
              </td>
              <td className="table-cell text-right text-sm">{quantity(c.planned_quantity, c.unit)}</td>
              <td className={`table-cell text-right text-sm ${Number(c.quantity) !== Number(c.planned_quantity) ? "font-semibold text-amber-700" : ""}`}>{quantity(c.quantity, c.unit)}</td>
              <td className="table-cell text-right text-sm">{rupees(c.rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {e.notes ? <p className="text-sm text-gray-600">{e.notes}</p> : null}
      {cancelling ? (
        <ReasonModal title="Cancel this entry" label="Why? Its stock movements will be reversed." button="Cancel entry" onClose={() => setCancelling(false)}
          send={async (reason) => {
            unwrap(await api.POST("/api/v1/production/entries/{entry_id}/cancel", { params: { path: { entry_id: e.id } }, body: { reason } }));
            for (const key of ["production-entries", "stock", "items"]) queryClient.invalidateQueries({ queryKey: [key] });
            setCancelling(false);
          }} />
      ) : null}
    </Modal>
  );
}
