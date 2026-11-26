import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, PageHeader } from "../../components/ui";
import { rupees } from "../../lib/format";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { todayIso } from "../purchase/PurchaseLayout";
import { salesOrderKey, useSalesOrder } from "./SalesLayout";

type Line = { key: number; item_id: number | null; quantity: string; rate: string; gst_rate: string; description: string };
let nextKey = 1;
const blankLine = (): Line => ({ key: nextKey++, item_id: null, quantity: "", rate: "", gst_rate: "", description: "" });

/** A sales order entered by hand: new (/sales/orders/new) or a draft (/sales/orders/:id/edit). */
export function SalesOrderEditor() {
  const { orderId } = useParams({ strict: false }) as { orderId?: string };
  const existing = useSalesOrder(orderId ?? "0", !!orderId);
  if (orderId && existing.isLoading) return <Loading />;
  if (orderId && existing.error) return <ErrorText error={existing.error} />;
  if (orderId && existing.data && !existing.data.actions.edit) {
    return <Empty title="This order can't be changed">Only a draft can be edited. <Link to="/sales/orders/$orderId" params={{ orderId }} className="btn-link">Back to the order</Link></Empty>;
  }
  return <SalesOrderForm order={orderId ? existing.data ?? null : null} />;
}

type Order = NonNullable<ReturnType<typeof useSalesOrder>["data"]>;

function SalesOrderForm({ order }: { order: Order | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const customers = useQuery({ queryKey: ["sales-customers"], queryFn: async () => unwrap(await api.GET("/api/v1/sales/customers")) });
  const items = useItems();
  const warehouses = useWarehouses();
  const activeItems = (items.data ?? []).filter((i) => i.is_active);
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);

  const [customerId, setCustomerId] = useState<number | null>(order?.customer.id ?? null);
  const [fromId, setFromId] = useState<number | null>(order?.dispatch_from_id ?? null);
  const [orderDate, setOrderDate] = useState(order?.order_date ?? todayIso());
  const [delivery, setDelivery] = useState(order?.delivery_date ?? "");
  const [customerPo, setCustomerPo] = useState(order?.customer_po_number ?? "");
  const [shipTo, setShipTo] = useState(order?.ship_to ?? "");
  const [terms, setTerms] = useState(order?.payment_terms_days != null ? String(order.payment_terms_days) : "");
  const [notes, setNotes] = useState(order?.notes ?? "");
  const [lines, setLines] = useState<Line[]>(
    order
      ? order.lines.map((l) => ({ key: nextKey++, item_id: l.item_id, quantity: String(Number(l.quantity)), rate: String(Number(l.rate)), gst_rate: String(Number(l.gst_rate)), description: l.description }))
      : [blankLine()],
  );
  const warehouseId = fromId ?? activeWarehouses.find((w) => w.is_default)?.id ?? activeWarehouses[0]?.id ?? null;
  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pickItem = (key: number, itemId: number | null) => {
    const item = activeItems.find((i) => i.id === itemId);
    setLine(key, { item_id: itemId, rate: item?.sale_price != null ? String(Number(item.sale_price)) : "", gst_rate: item ? String(Number(item.gst_rate)) : "" });
  };
  const preview = lines.reduce((sum, l) => {
    const taxable = (Number(l.quantity) || 0) * (Number(l.rate) || 0);
    return { taxable: sum.taxable + taxable, tax: sum.tax + (taxable * (Number(l.gst_rate) || 0)) / 100 };
  }, { taxable: 0, tax: 0 });

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        customer_id: customerId!, dispatch_from_id: warehouseId!, order_date: orderDate, delivery_date: delivery || null,
        customer_po_number: customerPo, ship_to: shipTo, payment_terms_days: terms.trim() === "" ? null : Number(terms), notes,
        lines: lines.filter((l) => l.item_id !== null).map((l) => ({
          item_id: l.item_id!, quantity: l.quantity, rate: l.rate === "" ? null : l.rate, gst_rate: l.gst_rate === "" ? null : l.gst_rate, description: l.description,
        })),
      };
      return order
        ? unwrap(await api.PUT("/api/v1/sales-orders/{order_id}", { params: { path: { order_id: order.id } }, body }))
        : unwrap(await api.POST("/api/v1/sales-orders", { body }));
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
      queryClient.setQueryData(salesOrderKey(saved.id), saved);
      navigate({ to: "/sales/orders/$orderId", params: { orderId: String(saved.id) } });
    },
  });

  if (customers.isLoading || items.isLoading || warehouses.isLoading) return <Loading />;
  if ((customers.data ?? []).length === 0) {
    return <Empty title="No customers yet">Add a customer under Partners first. <Link to="/partners" className="btn-link">Go to Partners</Link></Empty>;
  }
  if (activeWarehouses.length === 0 || activeItems.length === 0) {
    return <Empty title="Set up inventory first">A sales order needs items and a warehouse to dispatch from. <Link to="/inventory/items" className="btn-link">Go to Inventory</Link></Empty>;
  }
  const ready = customerId !== null && warehouseId !== null && lines.some((l) => l.item_id !== null && Number(l.quantity) > 0);

  return (
    <div className="space-y-6">
      <PageHeader title={order ? `Edit ${order.number}` : "New sales order"} description="Saved as a draft. Confirm it from the order page." />
      <Card>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Customer">
            {(id) => (
              <select id={id} className="field-input" value={customerId ?? ""} onChange={(e) => {
                const next = e.target.value ? Number(e.target.value) : null;
                setCustomerId(next);
                const c = (customers.data ?? []).find((x) => x.id === next);
                if (c?.payment_terms_days != null && terms === "") setTerms(String(c.payment_terms_days));
                if (c && !shipTo) setShipTo(c.address);
              }}>
                <option value="">Choose a customer</option>
                {(customers.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}{c.gstin ? ` · ${c.gstin}` : ""}</option>)}
              </select>
            )}
          </Field>
          <Field label="Dispatch from">
            {(id) => (
              <select id={id} className="field-input" value={warehouseId ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
                {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Customer's PO number">{(id) => <input id={id} className="field-input" value={customerPo} onChange={(e) => setCustomerPo(e.target.value)} maxLength={40} />}</Field>
          <Field label="Order date">{(id) => <input id={id} type="date" className="field-input" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} />}</Field>
          <Field label="Deliver by">{(id) => <input id={id} type="date" className="field-input" value={delivery} onChange={(e) => setDelivery(e.target.value)} />}</Field>
          <Field label="Payment terms (days)">{(id) => <input id={id} className="field-input" inputMode="numeric" value={terms} onChange={(e) => setTerms(e.target.value.replace(/\D/g, ""))} />}</Field>
          <div className="sm:col-span-3">
            <Field label="Ship to" hint="The customer's delivery address. Their state sets the place of supply for GST.">
              {(id) => <textarea id={id} className="field-input" rows={2} value={shipTo} onChange={(e) => setShipTo(e.target.value)} />}
            </Field>
          </div>
        </div>
      </Card>

      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
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
                    {lines.length > 1 ? <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove line ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button> : null}
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
      <Card><Field label="Notes">{(id) => <textarea id={id} className="field-input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field></Card>
      <ErrorText error={save.error} />
      <div className="flex justify-end gap-3">
        <Link to={order ? "/sales/orders/$orderId" : "/sales/orders"} params={order ? { orderId: String(order.id) } : {}} className="btn-secondary">Cancel</Link>
        <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Save draft</button>
      </div>
    </div>
  );
}
