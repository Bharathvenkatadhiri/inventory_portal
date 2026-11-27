import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { useItems } from "../inventory/InventoryLayout";

type Bom = Schemas["BomOut"];

export function BomsPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("bom.manage") && writable;
  const boms = useQuery({ queryKey: ["boms"], queryFn: async () => unwrap(await api.GET("/api/v1/boms")) });
  const [editing, setEditing] = useState<Bom | "new" | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Bills of materials"
        description="What goes into each item you make. Production entries use the default BOM's quantities, scaled to how many were made."
        actions={manage ? <button type="button" className="btn-primary" onClick={() => setEditing("new")}>New BOM</button> : null}
      />
      {boms.isLoading ? <Loading /> : boms.error ? <ErrorText error={boms.error} /> : (boms.data ?? []).length === 0 ? (
        <Empty title="No bills of materials yet">{manage ? "Add one for each finished good or part you make." : null}</Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {(boms.data ?? []).map((b) => (
            <Card key={b.id} className={b.is_active ? "" : "opacity-60"}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-navy-900">{b.item_name} <span className="text-sm font-normal text-gray-500">{b.item_code}</span></p>
                  <p className="text-sm text-gray-500">{b.name} · for {quantity(b.output_quantity, b.unit)} · about {rupees(b.estimated_unit_cost)} a unit in materials</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {b.is_default ? <Badge tone="navy">Default</Badge> : null}
                  {!b.is_active ? <Badge>Inactive</Badge> : null}
                  {b.editable ? <button type="button" className="btn-link" onClick={() => setEditing(b)}>Edit</button> : null}
                </div>
              </div>
              <ul className="mt-3 space-y-1 text-sm text-gray-700">
                {b.components.map((c) => <li key={c.item_id}>{quantity(c.quantity, c.unit)} {c.item_name} <span className="text-gray-500">{c.item_code}</span></li>)}
              </ul>
            </Card>
          ))}
        </div>
      )}
      {editing ? <BomForm bom={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

type Line = { key: number; item_id: number | null; quantity: string };
let nextKey = 1;

function BomForm({ bom, onClose }: { bom: Bom | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const items = useItems();
  const active = (items.data ?? []).filter((i) => i.is_active && i.tracks_stock);
  const makeable = active.filter((i) => ["finished_good", "semi_finished", "component"].includes(i.kind));
  const [itemId, setItemId] = useState<number | null>(bom?.item_id ?? null);
  const [name, setName] = useState(bom?.name ?? "Standard");
  const [output, setOutput] = useState(bom ? String(Number(bom.output_quantity)) : "1");
  const [notes, setNotes] = useState(bom?.notes ?? "");
  const [isDefault, setIsDefault] = useState(bom?.is_default ?? false);
  const [isActive, setIsActive] = useState(bom?.is_active ?? true);
  const [lines, setLines] = useState<Line[]>(bom ? bom.components.map((c) => ({ key: nextKey++, item_id: c.item_id, quantity: String(Number(c.quantity)) }))
    : [{ key: nextKey++, item_id: null, quantity: "" }]);
  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const done = () => { queryClient.invalidateQueries({ queryKey: ["boms"] }); onClose(); };
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        item_id: itemId!, name, output_quantity: output, notes, is_default: isDefault, is_active: isActive,
        components: lines.filter((l) => l.item_id !== null).map((l) => ({ item_id: l.item_id!, quantity: l.quantity })),
      };
      return bom ? unwrap(await api.PUT("/api/v1/boms/{bom_id}", { params: { path: { bom_id: bom.id } }, body }))
        : unwrap(await api.POST("/api/v1/boms", { body }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/boms/{bom_id}", { params: { path: { bom_id: bom!.id } } })),
    onSuccess: done,
  });
  const product = active.find((i) => i.id === itemId);

  return (
    <Modal wide title={bom ? `Edit BOM: ${bom.item_name}` : "New bill of materials"} onClose={onClose} footer={
      <>
        {bom && bom.entries === 0 ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!itemId || save.isPending} onClick={() => save.mutate()}>Save</button>
      </>
    }>
      {items.isLoading ? <Loading /> : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="sm:col-span-2">
              <Field label="Makes">
                {(id) => (
                  <select id={id} className="field-input" value={itemId ?? ""} onChange={(e) => setItemId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Choose the item made</option>
                    {makeable.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                  </select>
                )}
              </Field>
            </div>
            <Field label={`Quantity${product ? ` (${product.unit})` : ""}`} hint="The components below are for this many.">
              {(id) => <input id={id} className="field-input" inputMode="decimal" value={output} onChange={(e) => setOutput(e.target.value)} />}
            </Field>
            <Field label="Name" hint="E.g. Standard, Rev B.">{(id) => <input id={id} className="field-input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}</Field>
          </div>
          <table className="w-full">
            <thead className="border-b border-gray-100">
              <tr><th scope="col" className="table-head">Component</th><th scope="col" className="table-head w-40">Quantity</th><th scope="col" className="table-head w-10"><span className="sr-only">Remove</span></th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {lines.map((l, index) => {
                const part = active.find((i) => i.id === l.item_id);
                return (
                  <tr key={l.key}>
                    <td className="table-cell">
                      <select className="field-input" aria-label={`Component ${index + 1}`} value={l.item_id ?? ""} onChange={(e) => setLine(l.key, { item_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Choose a component</option>
                        {active.filter((i) => i.id !== itemId).map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                      </select>
                    </td>
                    <td className="table-cell">
                      <div className="flex items-center gap-1">
                        <input className="field-input" inputMode="decimal" aria-label={`Quantity, component ${index + 1}`} value={l.quantity} onChange={(e) => setLine(l.key, { quantity: e.target.value })} />
                        <span className="text-xs text-gray-500">{part?.unit}</span>
                      </div>
                    </td>
                    <td className="table-cell">{lines.length > 1 ? <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove component ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button type="button" className="btn-link" onClick={() => setLines([...lines, { key: nextKey++, item_id: null, quantity: "" }])}>+ Add a component</button>
          <Field label="Notes">{(id) => <textarea id={id} className="field-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
          <div className="flex flex-wrap gap-6 text-sm">
            <label className="flex items-center gap-2"><input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />Default BOM for this item</label>
            {bom ? <label className="flex items-center gap-2"><input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />Active</label> : null}
          </div>
        </>
      )}
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
