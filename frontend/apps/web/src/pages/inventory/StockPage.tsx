import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { useItems, useWarehouses } from "./InventoryLayout";
import { splitSerials } from "./batches";

type Opening = Schemas["OpeningOut"];

export function StockPage() {
  const can = useCan();
  const writable = useWritable();
  const adjust = can("stock.adjust") && writable;
  const warehouses = useWarehouses();
  const allLocations = useWarehouses(true);
  const [warehouseId, setWarehouseId] = useState<number | null>(null);
  const stock = useQuery({
    queryKey: ["stock", warehouseId],
    queryFn: async () => unwrap(await api.GET("/api/v1/stock", { params: { query: warehouseId ? { warehouse_id: warehouseId } : {} } })),
  });
  const opening = useQuery({
    queryKey: ["stock", "opening"],
    queryFn: async () => unwrap(await api.GET("/api/v1/stock/opening")),
  });
  const [editing, setEditing] = useState<Opening | "new" | null>(null);
  const rows = stock.data ?? [];
  const total = rows.reduce((sum, r) => sum + Number(r.value), 0);
  const noWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active).length === 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Stock"
        description="What's on hand in each warehouse, valued at cost before GST. Goods receipts and dispatches will update it; for now, enter what you hold today as opening stock."
        actions={adjust ? <button type="button" className="btn-primary" disabled={noWarehouses} onClick={() => setEditing("new")}>Enter opening stock</button> : null}
      />
      {adjust && noWarehouses && !warehouses.isLoading ? (
        <p className="text-sm text-gray-600">Add a warehouse first (Inventory → Warehouses).</p>
      ) : null}

      <Card
        title="On hand"
        actions={
          <select className="field-input w-auto" aria-label="Warehouse" value={warehouseId ?? ""} onChange={(e) => setWarehouseId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">All warehouses</option>
            {(allLocations.data?.warehouses ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        }
        className="overflow-x-auto"
      >
        {stock.isLoading ? (
          <Loading />
        ) : stock.error ? (
          <ErrorText error={stock.error} />
        ) : rows.length === 0 ? (
          <Empty title="No stock yet">{adjust ? "Enter opening stock to start." : null}</Empty>
        ) : (
          <table className="w-full min-w-[560px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Item</th>
                <th scope="col" className="table-head">Warehouse</th>
                <th scope="col" className="table-head text-right">Quantity</th>
                <th scope="col" className="table-head text-right">Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => (
                <tr key={`${r.item_id}-${r.warehouse_id}`}>
                  <td className="table-cell"><p className="font-semibold text-navy-900">{r.item_name}</p><p className="text-xs text-gray-500">{r.item_code}</p></td>
                  <td className="table-cell text-sm">{r.warehouse_name}</td>
                  <td className="table-cell text-right text-sm">{quantity(r.quantity, r.unit)}</td>
                  <td className="table-cell text-right text-sm">{rupees(r.value)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot className="border-t border-gray-200">
              <tr>
                <td className="table-cell font-semibold" colSpan={3}>Total value</td>
                <td className="table-cell text-right font-semibold">{rupees(total)}</td>
              </tr>
            </tfoot>
          </table>
        )}
      </Card>

      <Card title="Opening stock" description="What each warehouse held when you started on MakeSetu. You can correct it any time for now." className="overflow-x-auto">
        {opening.isLoading ? (
          <Loading />
        ) : (opening.data ?? []).length === 0 ? (
          <p className="text-sm text-gray-500">None entered yet.</p>
        ) : (
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Item</th>
                <th scope="col" className="table-head">Warehouse</th>
                <th scope="col" className="table-head text-right">Quantity × rate</th>
                <th scope="col" className="table-head">As of</th>
                <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(opening.data ?? []).map((o) => (
                <tr key={o.id}>
                  <td className="table-cell text-sm">
                    <span className="font-semibold text-navy-900">{o.item_name}</span> <span className="text-gray-500">{o.item_code}</span>
                    {o.batch_number ? <p className="font-mono text-xs text-gray-500">{o.batch_number}{o.expires_on ? ` · exp ${day(o.expires_on)}` : ""}</p> : null}
                  </td>
                  <td className="table-cell text-sm">{o.warehouse_name}</td>
                  <td className="table-cell text-right text-sm">{quantity(o.quantity, o.unit)} × {rupees(o.rate)}</td>
                  <td className="table-cell text-sm">{day(o.entry_date)}</td>
                  <td className="table-cell text-right">{adjust ? <button type="button" className="btn-link" onClick={() => setEditing(o)}>Edit</button> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {editing ? <OpeningForm entry={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function OpeningForm({ entry, onClose }: { entry: Opening | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const items = useItems();
  const warehouses = useWarehouses();
  const stockItems = (items.data ?? []).filter((i) => i.tracks_stock && i.is_active);
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [itemId, setItemId] = useState<number | null>(entry?.item_id ?? null);
  const [warehouseId, setWarehouseId] = useState<number | null>(entry?.warehouse_id ?? activeWarehouses.find((w) => w.is_default)?.id ?? null);
  const [qty, setQty] = useState(entry ? String(Number(entry.quantity)) : "");
  const [rate, setRate] = useState(entry ? String(Number(entry.rate)) : "");
  const [asOf, setAsOf] = useState(entry?.entry_date ?? new Date().toISOString().slice(0, 10));
  const chosen = stockItems.find((i) => i.id === itemId);
  const tracking = chosen?.tracking ?? "none";
  const [batchNumber, setBatchNumber] = useState(entry?.batch_number ?? "");
  const [expires, setExpires] = useState(entry?.expires_on ?? "");
  const [serials, setSerials] = useState("");
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["stock"] });
    queryClient.invalidateQueries({ queryKey: ["items"] });
    queryClient.invalidateQueries({ queryKey: ["warehouses"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async (quantityValue: string) => {
      const result = await api.PUT("/api/v1/stock/opening", {
        body: {
          item_id: itemId!, warehouse_id: (entry?.warehouse_id ?? warehouseId)!, quantity: quantityValue, rate: rate || "0", entry_date: asOf,
          note: entry?.note ?? "", supplier_batch: "", batch_number: tracking === "batch" ? batchNumber : "", expires_on: tracking === "batch" && expires ? expires : null,
          serials: tracking === "serial" && quantityValue !== "0" ? splitSerials(serials) : [],
        },
      });
      return result.response.status === 204 ? null : unwrap(result);
    },
    onSuccess: done,
  });
  const ready = itemId !== null && (entry || warehouseId !== null) && (qty.trim() !== "" || (tracking === "serial" && splitSerials(serials).length > 0))
    && (tracking !== "batch" || batchNumber.trim() !== "");

  return (
    <Modal
      title={entry ? `Opening stock: ${entry.item_name}` : "Enter opening stock"}
      onClose={onClose}
      footer={
        <>
          {entry ? <button type="button" className="btn-danger mr-auto" onClick={() => save.mutate("0")}>Remove</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate(qty)}>Save</button>
        </>
      }
    >
      <Field label="Item">
        {(id) => (
          <select id={id} className="field-input" value={itemId ?? ""} disabled={!!entry} onChange={(e) => {
            const next = e.target.value ? Number(e.target.value) : null;
            setItemId(next);
            const picked = stockItems.find((i) => i.id === next);
            if (picked?.purchase_price != null && !rate) setRate(String(picked.purchase_price));
          }}>
            <option value="">Choose an item</option>
            {(entry ? [{ id: entry.item_id, code: entry.item_code, name: entry.item_name }] : stockItems).map((i) => (
              <option key={i.id} value={i.id}>{i.code} · {i.name}</option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Warehouse">
        {(id) => (
          <select id={id} className="field-input" value={(entry?.warehouse_id ?? warehouseId) ?? ""} disabled={!!entry}
            onChange={(e) => setWarehouseId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Choose a warehouse</option>
            {(entry ? [{ id: entry.warehouse_id, name: entry.warehouse_name }] : activeWarehouses).map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={`Quantity${chosen || entry ? ` (${chosen?.unit ?? entry?.unit})` : ""}`} hint="Replaces any opening stock already entered here.">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />}
        </Field>
        <Field label="Rate (₹ per unit)" hint="Cost before GST.">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />}
        </Field>
      </div>
      {tracking === "batch" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Batch number" hint="Opening stock is entered batch by batch.">
            {(id) => <input id={id} className="field-input font-mono" value={batchNumber} disabled={!!entry} maxLength={60} onChange={(e) => setBatchNumber(e.target.value)} />}
          </Field>
          <Field label="Expires on" hint="Optional.">{(id) => <input id={id} type="date" className="field-input" value={expires} onChange={(e) => setExpires(e.target.value)} />}</Field>
        </div>
      ) : null}
      {tracking === "serial" ? (
        <Field label="Serial numbers" hint="One per line. They replace this item's opening serial numbers here; leave blank to number them from the item code.">
          {(id) => <textarea id={id} className="field-input font-mono text-xs" rows={3} value={serials} onChange={(e) => setSerials(e.target.value)} />}
        </Field>
      ) : null}
      <Field label="As of">{(id) => <input id={id} type="date" className="field-input" value={asOf} onChange={(e) => setAsOf(e.target.value)} />}</Field>
      <ErrorText error={save.error} />
    </Modal>
  );
}
