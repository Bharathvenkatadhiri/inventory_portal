import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Meter, Modal, PageHeader } from "../../components/ui";
import { useCan, useWritable } from "../../lib/session";
import { useWarehouses } from "./InventoryLayout";

type Warehouse = Schemas["WarehouseOut"];

export function WarehousesPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("warehouses.manage") && writable;
  const warehouses = useWarehouses();
  const [editing, setEditing] = useState<Warehouse | "new" | null>(null);
  if (warehouses.isLoading) return <Loading />;
  if (warehouses.error || !warehouses.data) return <ErrorText error={warehouses.error} />;
  const { used, included } = warehouses.data;
  const atLimit = included !== null && included !== undefined && used >= included;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Warehouses"
        description="Stores, godowns and the shop floor: anywhere stock is kept. Goods received go to the default warehouse unless a document says otherwise."
        actions={manage ? <button type="button" className="btn-primary" disabled={atLimit} onClick={() => setEditing("new")}>Add a warehouse</button> : null}
      />
      <Card>
        <Meter
          label="Active warehouses"
          used={used}
          limit={included}
          note={atLimit ? (
            <>Your plan's warehouses are all in use. {can("billing.manage") ? <Link to="/settings/billing" className="font-semibold text-accent-600 underline">Move to a bigger plan</Link> : "Ask your Org Admin about a bigger plan."}</>
          ) : undefined}
        />
      </Card>
      {warehouses.data.warehouses.length === 0 ? (
        <Empty title="No warehouses yet">{manage ? "Add the store where you keep stock; it becomes your default." : "Ask someone who manages warehouses to add one."}</Empty>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {warehouses.data.warehouses.map((w) => (
            <li key={w.id} className={`card ${w.is_active ? "" : "opacity-60"}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-navy-900">
                    {w.name} {w.code ? <span className="text-sm font-normal text-gray-500">({w.code})</span> : null}
                  </p>
                  <p className="mt-1 text-sm text-gray-500">{w.address || "No address set"}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {w.is_default ? <Badge tone="navy">Default</Badge> : null}
                  {!w.is_active ? <Badge>Inactive</Badge> : null}
                  {manage ? <button type="button" className="btn-link" onClick={() => setEditing(w)}>Edit</button> : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {editing ? <WarehouseForm warehouse={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function WarehouseForm({ warehouse, onClose }: { warehouse: Warehouse | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const addresses = useQuery({
    queryKey: ["addresses"],
    queryFn: async () => unwrap(await api.GET("/api/v1/organization/addresses")),
  });
  const [name, setName] = useState(warehouse?.name ?? "");
  const [code, setCode] = useState(warehouse?.code ?? "");
  const [addressId, setAddressId] = useState<number | null>(warehouse?.address_id ?? null);
  const [isDefault, setIsDefault] = useState(warehouse?.is_default ?? false);
  const [isActive, setIsActive] = useState(warehouse?.is_active ?? true);
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["warehouses"] });
    queryClient.invalidateQueries({ queryKey: ["stock"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () => {
      // Only send is_default when it's being turned on: the server moves the default, never leaves none.
      const body = { name, code, address_id: addressId, is_active: isActive, is_default: isDefault && !warehouse?.is_default ? true : null };
      return warehouse
        ? unwrap(await api.PUT("/api/v1/warehouses/{warehouse_id}", { params: { path: { warehouse_id: warehouse.id } }, body }))
        : unwrap(await api.POST("/api/v1/warehouses", { body }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/warehouses/{warehouse_id}", { params: { path: { warehouse_id: warehouse!.id } } })),
    onSuccess: done,
  });

  return (
    <Modal
      title={warehouse ? `Edit ${warehouse.name}` : "Add a warehouse"}
      onClose={onClose}
      footer={
        <>
          {warehouse && !warehouse.has_stock ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <Field label="Name">{(id) => <input id={id} className="field-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Main store" maxLength={80} />}</Field>
      <Field label="Short code" hint="Optional, e.g. MS or FG.">
        {(id) => <input id={id} className="field-input uppercase" value={code} onChange={(e) => setCode(e.target.value)} maxLength={20} />}
      </Field>
      <Field label="Address" hint="Add addresses under Settings → Company.">
        {(id) => (
          <select id={id} className="field-input" value={addressId ?? ""} onChange={(e) => setAddressId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">No address</option>
            {(addresses.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>{[a.label, a.line1, a.city].filter(Boolean).join(", ")}</option>
            ))}
          </select>
        )}
      </Field>
      {!warehouse?.is_default ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
          Make this the default warehouse
        </label>
      ) : (
        <p className="text-sm text-gray-500">This is the default warehouse. To change that, edit another warehouse and make it the default.</p>
      )}
      {warehouse && !warehouse.is_default ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Active
        </label>
      ) : null}
      {warehouse?.has_stock ? <p className="text-xs text-gray-500">It has stock, so it can be made inactive but not deleted.</p> : null}
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
