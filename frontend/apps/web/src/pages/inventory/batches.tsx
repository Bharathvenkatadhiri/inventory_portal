import { api, unwrap, type Schemas } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { day, quantity } from "../../lib/format";
import { useItems } from "./InventoryLayout";

/** Batch and serial number inputs shared by the documents that move stock. */

export type Tracking = "none" | "batch" | "serial" | string;
export type BatchRow = { number: string; quantity: string; supplier_batch: string; expires_on: string };
type BatchQty = Schemas["BatchQtyOut"];

export const emptyBatch = (): BatchRow => ({ number: "", quantity: "", supplier_batch: "", expires_on: "" });

/** "H-7 (150), H-8 (100)", or the serial numbers. */
export function batchText(batches: BatchQty[] | undefined, tracking?: Tracking) {
  return (batches ?? []).map((b) => (tracking === "serial" ? b.number : `${b.number} (${quantity(b.quantity)})`)).join(", ");
}

/** What the API takes for batches entered on stock coming in. */
export function batchesIn(tracking: Tracking, rows: BatchRow[], serials: string) {
  if (tracking === "serial") return splitSerials(serials).map((number) => ({ number, supplier_batch: "" }));
  if (tracking !== "batch") return [];
  return rows
    .filter((r) => r.number.trim())
    .map((r) => ({
      number: r.number.trim(),
      quantity: r.quantity.trim() || null,
      supplier_batch: r.supplier_batch.trim(),
      expires_on: r.expires_on || null,
    }));
}

export function splitSerials(text: string) {
  return text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
}

/**
 * Batches (or serial numbers) for stock coming in. Left empty, the server
 * numbers a batch from the document, or gives the next serial numbers.
 */
export function BatchesIn({ label, tracking, rows, onRows, serials, onSerials, heat = false }: {
  label: string; tracking: Tracking; rows: BatchRow[]; onRows: (rows: BatchRow[]) => void;
  serials: string; onSerials: (text: string) => void; heat?: boolean;
}) {
  if (tracking === "serial") {
    const count = splitSerials(serials).length;
    return (
      <div className="mt-2">
        <label className="text-xs font-semibold text-gray-600">
          Serial numbers{count ? ` (${count})` : ""}
          <textarea className="field-input mt-1 font-mono text-xs" rows={2} aria-label={`Serial numbers, ${label}`} value={serials}
            placeholder="Scan or type them, one per line. Leave blank to number them from the item code."
            onChange={(e) => onSerials(e.target.value)} />
        </label>
      </div>
    );
  }
  if (tracking !== "batch") return null;
  const set = (i: number, patch: Partial<BatchRow>) => onRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="mt-2 space-y-1">
      {rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1">
          <input className="field-input w-32 text-xs" aria-label={`Batch number ${i + 1}, ${label}`} placeholder="Batch no." value={r.number}
            onChange={(e) => set(i, { number: e.target.value })} />
          {rows.length > 1 ? (
            <input className="field-input w-20 text-xs" inputMode="decimal" aria-label={`Batch ${i + 1} quantity, ${label}`} placeholder="Qty"
              value={r.quantity} onChange={(e) => set(i, { quantity: e.target.value })} />
          ) : null}
          {heat ? (
            <input className="field-input w-28 text-xs" aria-label={`Supplier's batch or heat number ${i + 1}, ${label}`} placeholder="Heat / their batch"
              value={r.supplier_batch} onChange={(e) => set(i, { supplier_batch: e.target.value })} />
          ) : null}
          <input type="date" className="field-input w-36 text-xs" aria-label={`Batch ${i + 1} expiry, ${label}`} title="Expires on (optional)"
            value={r.expires_on} onChange={(e) => set(i, { expires_on: e.target.value })} />
          {rows.length > 1 ? (
            <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove batch ${i + 1}`}
              onClick={() => onRows(rows.filter((_, j) => j !== i))}>✕</button>
          ) : null}
        </div>
      ))}
      <button type="button" className="btn-link text-xs" onClick={() => onRows([...rows, emptyBatch()])}>+ Split into another batch</button>
      <p className="text-xs text-gray-500">Leave the number blank to use the document's number.</p>
    </div>
  );
}

export type Pick = { batch_id: number; quantity: string };

/**
 * Batches (or serial numbers) for stock going out. Nothing picked means the
 * first to expire go first, which is what most people want.
 */
export function BatchPicker({ label, tracking, itemId, warehouseId, picks, onPicks }: {
  label: string; tracking: Tracking; itemId: number; warehouseId: number | null | undefined;
  picks: Pick[] | null; onPicks: (picks: Pick[] | null) => void;
}) {
  const choosing = picks !== null;
  const stock = useQuery({
    queryKey: ["batches", "in", itemId, warehouseId],
    enabled: choosing && !!warehouseId && tracking !== "none",
    queryFn: async () => unwrap(await api.GET("/api/v1/batches", { params: { query: { item_id: itemId, warehouse_id: warehouseId! } } })),
  });
  if (tracking !== "batch" && tracking !== "serial") return null;
  if (!choosing) {
    return (
      <p className="mt-1 text-xs text-gray-500">
        First to expire go first. <button type="button" className="btn-link text-xs" onClick={() => onPicks([])}>Choose {tracking === "serial" ? "serial numbers" : "batches"}</button>
      </p>
    );
  }
  const here = (b: Schemas["BatchOut"]) => b.stock.find((s) => s.warehouse_id === warehouseId)?.quantity ?? "0";
  const picked = (id: number) => picks.find((p) => p.batch_id === id);
  const setPick = (id: number, qty: string) =>
    onPicks(qty ? [...picks.filter((p) => p.batch_id !== id), { batch_id: id, quantity: qty }] : picks.filter((p) => p.batch_id !== id));
  return (
    <div className="mt-2 rounded border border-gray-200 p-2">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-semibold text-gray-600">
          {tracking === "serial" ? `Serial numbers (${picks.length} picked)` : "Batches"}
        </span>
        <button type="button" className="btn-link text-xs" onClick={() => onPicks(null)}>Automatic instead</button>
      </div>
      {stock.isLoading ? <p className="text-xs text-gray-500">Loading…</p> : (stock.data ?? []).length === 0 ? (
        <p className="text-xs text-gray-500">None in stock here.</p>
      ) : (
        <ul className="max-h-40 space-y-1 overflow-y-auto">
          {(stock.data ?? []).map((b) => (
            <li key={b.id} className="flex items-center gap-2 text-xs">
              {tracking === "serial" ? (
                <label className="flex items-center gap-2">
                  <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={!!picked(b.id)}
                    onChange={(e) => setPick(b.id, e.target.checked ? "1" : "")} aria-label={`${label}, serial ${b.number}`} />
                  <span className="font-mono">{b.number}</span>
                </label>
              ) : (
                <>
                  <input className="field-input w-20 text-xs" inputMode="decimal" aria-label={`${label}, quantity from batch ${b.number}`}
                    value={picked(b.id)?.quantity ?? ""} onChange={(e) => setPick(b.id, e.target.value)} disabled={b.expired} />
                  <span className="font-mono">{b.number}</span>
                  <span className="text-gray-500">{quantity(here(b), b.unit)} here</span>
                </>
              )}
              {b.expires_on ? <span className={b.expired ? "font-semibold text-red-700" : "text-gray-500"}>{b.expired ? "expired" : "exp"} {day(b.expires_on)}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** For the API: undefined (automatic) or the picks. */
export function picksIn(picks: Pick[] | null | undefined) {
  return picks && picks.length ? picks.map((p) => ({ batch_id: p.batch_id, number: "", quantity: p.quantity })) : [];
}

/** How each item is tracked: tracking(itemId) is "none", "batch" or "serial". */
export function useTracking() {
  const items = useItems();
  const byId = new Map((items.data ?? []).map((i) => [i.id, i.tracking]));
  return (itemId: number): Tracking => byId.get(itemId) ?? "none";
}

/** Keeps per-line batch inputs for a document form. */
export function useLineBatches<K extends string | number>() {
  const [rows, setRows] = useState<Record<string, BatchRow[]>>({});
  const [serials, setSerials] = useState<Record<string, string>>({});
  const [picks, setPicks] = useState<Record<string, Pick[] | null>>({});
  return {
    rows: (k: K) => rows[String(k)] ?? [emptyBatch()],
    setRows: (k: K, value: BatchRow[]) => setRows((all) => ({ ...all, [String(k)]: value })),
    serials: (k: K) => serials[String(k)] ?? "",
    setSerials: (k: K, value: string) => setSerials((all) => ({ ...all, [String(k)]: value })),
    picks: (k: K) => picks[String(k)] ?? null,
    setPicks: (k: K, value: Pick[] | null) => setPicks((all) => ({ ...all, [String(k)]: value })),
  };
}
