import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, PageHeader } from "../../components/ui";
import { rupees } from "../../lib/format";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { orderKey, todayIso, useOrder } from "./PurchaseLayout";

type Line = { key: number; item_id: number | null; quantity: string; rate: string; gst_rate: string; description: string };
let nextKey = 1;
const blankLine = (): Line => ({ key: nextKey++, item_id: null, quantity: "", rate: "", gst_rate: "", description: "" });

/** New order (/purchase/orders/new) or editing a draft (/purchase/orders/:id/edit). */
export function OrderEditor() {
  const { orderId } = useParams({ strict: false }) as { orderId?: string };
  const existing = useOrder(orderId ?? "0", !!orderId);
  if (orderId && existing.isLoading) return <Loading />;
  if (orderId && existing.error) return <ErrorText error={existing.error} />;
  if (orderId && existing.data && !existing.data.actions.edit) {
    return <Empty title="This order can't be changed">Only a draft can be edited. <Link to="/purchase/orders/$orderId" params={{ orderId }} className="btn-link">Back to the order</Link></Empty>;
  }
  return <OrderForm order={orderId ? existing.data ?? null : null} />;
}

type Order = NonNullable<ReturnType<typeof useOrder>["data"]>;

function OrderForm({ order }: { order: Order | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const suppliers = useQuery({
    queryKey: ["purchase-suppliers"],
    queryFn: async () => unwrap(await api.GET("/api/v1/purchase/suppliers")),
  });
  const items = useItems();
  const warehouses = useWarehouses();
  const activeItems = (items.data ?? []).filter((i) => i.is_active);
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);

  const [supplierId, setSupplierId] = useState<number | null>(order?.supplier.id ?? null);
  const [shipToId, setShipToId] = useState<number | null>(order?.ship_to_id ?? null);
  const [orderDate, setOrderDate] = useState(order?.order_date ?? todayIso());
  const [expected, setExpected] = useState(order?.expected_date ?? "");
  const [terms, setTerms] = useState(order?.payment_terms_days != null ? String(order.payment_terms_days) : "");
  const [reference, setReference] = useState(order?.supplier_reference ?? "");
  const [notes, setNotes] = useState(order?.notes ?? "");
  const [lines, setLines] = useState<Line[]>(
    order
      ? order.lines.map((l) => ({ key: nextKey++, item_id: l.item_id, quantity: String(Number(l.quantity)), rate: String(Number(l.rate)), gst_rate: String(Number(l.gst_rate)), description: l.description }))
      : [blankLine()],
  );

  // Default the ship-to warehouse, and the supplier's payment terms when one is picked.
  useEffect(() => {
    if (shipToId === null && activeWarehouses.length) setShipToId((activeWarehouses.find((w) => w.is_default) ?? activeWarehouses[0]).id);
  }, [activeWarehouses, shipToId]);

  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pickItem = (key: number, itemId: number | null) => {
    const item = activeItems.find((i) => i.id === itemId);
    setLine(key, {
      item_id: itemId,
      rate: item?.purchase_price != null ? String(Number(item.purchase_price)) : "",
      gst_rate: item ? String(Number(item.gst_rate)) : "",
    });
  };

  // A preview; the server works out the final figures (and the CGST/SGST or IGST split).
  const preview = lines.reduce(
    (sum, l) => {
      const taxable = (Number(l.quantity) || 0) * (Number(l.rate) || 0);
      return { taxable: sum.taxable + taxable, tax: sum.tax + (taxable * (Number(l.gst_rate) || 0)) / 100 };
    },
    { taxable: 0, tax: 0 },
  );

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        supplier_id: supplierId!,
        ship_to_id: shipToId!,
        order_date: orderDate,
        expected_date: expected || null,
        payment_terms_days: terms.trim() === "" ? null : Number(terms),
        supplier_reference: reference,
        notes,
        lines: lines.filter((l) => l.item_id !== null).map((l) => ({
          item_id: l.item_id!, quantity: l.quantity, rate: l.rate === "" ? null : l.rate, gst_rate: l.gst_rate === "" ? null : l.gst_rate, description: l.description,
        })),
      };
      return order
        ? unwrap(await api.PUT("/api/v1/purchase-orders/{order_id}", { params: { path: { order_id: order.id } }, body }))
        : unwrap(await api.POST("/api/v1/purchase-orders", { body }));
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      queryClient.setQueryData(orderKey(saved.id), saved);
      navigate({ to: "/purchase/orders/$orderId", params: { orderId: String(saved.id) } });
    },
  });

  if (suppliers.isLoading || items.isLoading || warehouses.isLoading) return <Loading />;
  if ((suppliers.data ?? []).length === 0) {
    return <Empty title="No suppliers to order from yet">Add a supplier under Partners first; orders can go to active suppliers. <Link to="/partners" className="btn-link">Go to Partners</Link></Empty>;
  }
  if (activeWarehouses.length === 0 || activeItems.length === 0) {
    return <Empty title="Set up inventory first">An order needs at least one item and a warehouse to deliver to. <Link to="/inventory/items" className="btn-link">Go to Inventory</Link></Empty>;
  }
  const ready = supplierId !== null && shipToId !== null && lines.some((l) => l.item_id !== null && Number(l.quantity) > 0);

  return (
    <div className="space-y-6">
      <PageHeader title={order ? `Edit ${order.number}` : "New purchase order"} description="Saved as a draft. Issue it from the order page when it's ready." />
      <Card>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Supplier">
            {(id) => (
              <select id={id} className="field-input" value={supplierId ?? ""} onChange={(e) => {
                const next = e.target.value ? Number(e.target.value) : null;
                setSupplierId(next);
                const s = (suppliers.data ?? []).find((x) => x.id === next);
                if (s?.payment_terms_days != null && terms === "") setTerms(String(s.payment_terms_days));
              }}>
                <option value="">Choose a supplier</option>
                {(suppliers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}{s.gstin ? ` · ${s.gstin}` : ""}</option>)}
              </select>
            )}
          </Field>
          <Field label="Deliver to">
            {(id) => (
              <select id={id} className="field-input" value={shipToId ?? ""} onChange={(e) => setShipToId(e.target.value ? Number(e.target.value) : null)}>
                {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Payment terms (days)" hint="From the supplier's terms; change it for this order if needed.">
            {(id) => <input id={id} className="field-input" inputMode="numeric" value={terms} onChange={(e) => setTerms(e.target.value.replace(/\D/g, ""))} />}
          </Field>
          <Field label="Order date">{(id) => <input id={id} type="date" className="field-input" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} />}</Field>
          <Field label="Expected by">{(id) => <input id={id} type="date" className="field-input" value={expected} onChange={(e) => setExpected(e.target.value)} />}</Field>
          <Field label="Supplier's quote / reference">{(id) => <input id={id} className="field-input" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={100} />}</Field>
        </div>
      </Card>

      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head w-32">Quantity</th>
              <th scope="col" className="table-head w-32">Rate (₹)</th>
              <th scope="col" className="table-head w-28">GST %</th>
              <th scope="col" className="table-head w-32 text-right">Amount</th>
              <th scope="col" className="table-head w-10"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.map((l, index) => {
              const item = activeItems.find((i) => i.id === l.item_id);
              return (
                <tr key={l.key}>
                  <td className="table-cell">
                    <select className="field-input" aria-label={`Item, line ${index + 1}`} value={l.item_id ?? ""} onChange={(e) => pickItem(l.key, e.target.value ? Number(e.target.value) : null)}>
                      <option value="">Choose an item</option>
                      {activeItems.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                    </select>
                    <input className="field-input mt-1 text-xs" placeholder="Note for the supplier (optional)" aria-label={`Note, line ${index + 1}`}
                      value={l.description} onChange={(e) => setLine(l.key, { description: e.target.value })} maxLength={200} />
                  </td>
                  <td className="table-cell">
                    <div className="flex items-center gap-1">
                      <input className="field-input" inputMode="decimal" aria-label={`Quantity, line ${index + 1}`} value={l.quantity} onChange={(e) => setLine(l.key, { quantity: e.target.value })} />
                      <span className="text-xs text-gray-500">{item?.unit}</span>
                    </div>
                  </td>
                  <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Rate, line ${index + 1}`} value={l.rate} onChange={(e) => setLine(l.key, { rate: e.target.value })} /></td>
                  <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`GST rate, line ${index + 1}`} value={l.gst_rate} onChange={(e) => setLine(l.key, { gst_rate: e.target.value })} /></td>
                  <td className="table-cell text-right text-sm">{rupees((Number(l.quantity) || 0) * (Number(l.rate) || 0))}</td>
                  <td className="table-cell">
                    {lines.length > 1 ? (
                      <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove line ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <button type="button" className="btn-link mt-3" onClick={() => setLines([...lines, blankLine()])}>+ Add a line</button>
        <dl className="ml-auto mt-4 w-full max-w-xs space-y-1 text-sm">
          <div className="flex justify-between"><dt className="text-gray-500">Taxable value</dt><dd>{rupees(preview.taxable)}</dd></div>
          <div className="flex justify-between"><dt className="text-gray-500">GST</dt><dd>{rupees(preview.tax)}</dd></div>
          <div className="flex justify-between border-t border-gray-200 pt-1 font-semibold"><dt>Total (before rounding)</dt><dd>{rupees(preview.taxable + preview.tax)}</dd></div>
        </dl>
      </Card>

      <Card>
        <Field label="Notes and terms for the supplier">
          {(id) => <textarea id={id} className="field-input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Delivery instructions, inspection, packing…" />}
        </Field>
      </Card>

      <ErrorText error={save.error} />
      <div className="flex justify-end gap-3">
        <Link to={order ? "/purchase/orders/$orderId" : "/purchase/orders"} params={order ? { orderId: String(order.id) } : {}} className="btn-secondary">Cancel</Link>
        <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Save draft</button>
      </div>
    </div>
  );
}
