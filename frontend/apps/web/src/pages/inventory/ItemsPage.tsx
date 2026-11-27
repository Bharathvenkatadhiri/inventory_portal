import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { optionalNumber, useInventoryOptions, useItems } from "./InventoryLayout";

type Item = Schemas["ItemOut"];

export function ItemsPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("items.manage") && writable;
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const items = useItems({ q, kind });
  const options = useInventoryOptions();
  const [editing, setEditing] = useState<Item | "new" | null>(null);
  const seeStock = can("stock.view");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Items"
        description="Raw materials, bought-out parts, finished goods and services: everything you buy, make, stock or sell. Purchase and sales documents pick from this list."
        actions={manage ? <button type="button" className="btn-primary" onClick={() => setEditing("new")}>Add an item</button> : null}
      />
      <div className="flex flex-wrap gap-3">
        <input
          type="search"
          className="field-input max-w-xs"
          placeholder="Search code, name or HSN"
          aria-label="Search items"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="field-input max-w-[200px]" aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All types</option>
          {(options.data?.kinds ?? []).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
      </div>
      <Card className="overflow-x-auto p-0">
        {items.isLoading ? (
          <Loading />
        ) : items.error ? (
          <div className="p-5"><ErrorText error={items.error} /></div>
        ) : (items.data ?? []).length === 0 ? (
          <div className="p-5">
            <Empty title={q || kind ? "No items match" : "No items yet"}>
              {!q && !kind && manage ? "Add the materials and parts you buy first; purchase orders will pick from them." : null}
            </Empty>
          </div>
        ) : (
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Item</th>
                <th scope="col" className="table-head">Type</th>
                <th scope="col" className="table-head">HSN/SAC · GST</th>
                <th scope="col" className="table-head text-right">Purchase / sale price</th>
                {seeStock ? <th scope="col" className="table-head text-right">On hand</th> : null}
                <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(items.data ?? []).map((item) => (
                <tr key={item.id} className={item.is_active ? "" : "opacity-60"}>
                  <td className="table-cell">
                    <p className="font-semibold text-navy-900">{item.name}</p>
                    <p className="text-xs text-gray-500">{item.code} · {item.unit}{item.is_active ? "" : " · inactive"}</p>
                  </td>
                  <td className="table-cell text-sm">{item.kind_label}</td>
                  <td className="table-cell text-sm">{item.hsn_code || "—"} · {Number(item.gst_rate)}%</td>
                  <td className="table-cell text-right text-sm">{rupees(item.purchase_price)} / {rupees(item.sale_price)}</td>
                  {seeStock ? (
                    <td className="table-cell text-right text-sm">
                      {item.tracks_stock ? quantity(item.on_hand, item.unit) : "—"}
                      {item.below_reorder ? <div className="mt-1"><Badge tone="amber">Reorder</Badge></div> : null}
                    </td>
                  ) : null}
                  <td className="table-cell text-right">
                    {manage ? <button type="button" className="btn-link" onClick={() => setEditing(item)}>Edit</button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {editing ? <ItemForm item={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function ItemForm({ item, onClose }: { item: Item | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const options = useInventoryOptions();
  const [form, setForm] = useState({
    code: item?.code ?? "",
    name: item?.name ?? "",
    kind: item?.kind ?? "raw_material",
    unit: item?.unit ?? "NOS",
    hsn_code: item?.hsn_code ?? "",
    gst_rate: item ? String(Number(item.gst_rate)) : "18",
    purchase_price: item?.purchase_price != null ? String(item.purchase_price) : "",
    sale_price: item?.sale_price != null ? String(item.sale_price) : "",
    reorder_level: item?.reorder_level != null ? String(Number(item.reorder_level)) : "",
    description: item?.description ?? "",
    is_active: item?.is_active ?? true,
    inspection_required: item?.inspection_required ?? false,
    tracking: item?.tracking ?? "none",
    shelf_life_days: item?.shelf_life_days != null ? String(item.shelf_life_days) : "",
  });
  const qc = useCan()("inspection.record");
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });
  const isService = form.kind === "service";
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["items"] });
    queryClient.invalidateQueries({ queryKey: ["stock"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        ...form,
        purchase_price: optionalNumber(form.purchase_price),
        sale_price: optionalNumber(form.sale_price),
        reorder_level: isService ? null : optionalNumber(form.reorder_level),
        tracking: isService ? "none" : form.tracking,
        shelf_life_days: form.tracking === "none" || !form.shelf_life_days.trim() ? null : Number(form.shelf_life_days),
      };
      return item
        ? unwrap(await api.PUT("/api/v1/items/{item_id}", { params: { path: { item_id: item.id } }, body }))
        : unwrap(await api.POST("/api/v1/items", { body }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/items/{item_id}", { params: { path: { item_id: item!.id } } })),
    onSuccess: done,
  });

  return (
    <Modal
      wide
      title={item ? `Edit ${item.code}` : "Add an item"}
      onClose={onClose}
      footer={
        <>
          {item ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!form.code.trim() || !form.name.trim() || save.isPending} onClick={() => save.mutate()}>
            Save
          </button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Code" hint="Your own code, e.g. RM-MS-25.">
          {(id) => <input id={id} className="field-input uppercase" value={form.code} onChange={set("code")} maxLength={40} />}
        </Field>
        <div className="sm:col-span-2">
          <Field label="Name">{(id) => <input id={id} className="field-input" value={form.name} onChange={set("name")} maxLength={200} />}</Field>
        </div>
        <Field label="Type">
          {(id) => (
            <select id={id} className="field-input" value={form.kind} onChange={set("kind")}>
              {(options.data?.kinds ?? []).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Unit (GST UQC)">
          {(id) => (
            <select id={id} className="field-input" value={form.unit} onChange={set("unit")}>
              {(options.data?.units ?? []).map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          )}
        </Field>
        <Field label={isService ? "SAC code" : "HSN code"} hint={isService ? "6 digits starting with 99." : "4, 6 or 8 digits."}>
          {(id) => <input id={id} className="field-input" inputMode="numeric" value={form.hsn_code} onChange={set("hsn_code")} maxLength={8} />}
        </Field>
        <Field label="GST rate">
          {(id) => (
            <select id={id} className="field-input" value={form.gst_rate} onChange={set("gst_rate")}>
              {(options.data?.gst_rates ?? []).map((r) => <option key={r} value={r}>{r}%</option>)}
            </select>
          )}
        </Field>
        <Field label="Purchase price (₹)" hint="Per unit, before GST.">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={form.purchase_price} onChange={set("purchase_price")} />}
        </Field>
        <Field label="Sale price (₹)" hint="Per unit, before GST.">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={form.sale_price} onChange={set("sale_price")} />}
        </Field>
        {!isService ? (
          <Field label="Reorder level" hint="Flag the item when total stock falls to this.">
            {(id) => <input id={id} className="field-input" inputMode="decimal" value={form.reorder_level} onChange={set("reorder_level")} />}
          </Field>
        ) : null}
        <div className="sm:col-span-3">
          <Field label="Description">
            {(id) => <textarea id={id} className="field-input" rows={2} value={form.description} onChange={set("description")} />}
          </Field>
        </div>
      </div>
      {!isService && (options.data?.batches_available || form.tracking !== "none") ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Track stock" hint={item?.on_hand && Number(item.on_hand) !== 0 ? "Can't change while the item has stock." : "Batch or heat number, or a serial number on each unit."}>
            {(id) => (
              <select id={id} className="field-input" value={form.tracking} onChange={set("tracking")}
                disabled={!!item?.on_hand && Number(item.on_hand) !== 0}>
                {(options.data?.tracking ?? []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            )}
          </Field>
          {form.tracking !== "none" ? (
            <Field label="Shelf life (days)" hint="Fills in each batch's expiry. Leave blank if it doesn't expire.">
              {(id) => <input id={id} className="field-input" inputMode="numeric" value={form.shelf_life_days} onChange={set("shelf_life_days")} />}
            </Field>
          ) : null}
        </div>
      ) : null}
      {qc && !isService ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={form.inspection_required}
            onChange={(e) => setForm({ ...form, inspection_required: e.target.checked })} />
          Needs inspection (received or produced quantities wait for QC before they can be used)
        </label>
      ) : null}
      {item ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={form.is_active}
            onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
          Active (inactive items can't be used on new documents)
        </label>
      ) : null}
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
