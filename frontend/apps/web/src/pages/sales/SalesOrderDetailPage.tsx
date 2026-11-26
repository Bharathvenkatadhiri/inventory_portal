import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useWarehouses } from "../inventory/InventoryLayout";
import { ReasonModal, StatusBadge, todayIso, TotalsTable } from "../purchase/PurchaseLayout";
import { salesOrderKey, useSalesOrder } from "./SalesLayout";

type Order = Schemas["SalesOrderOut"];
type Action = "submit" | "approve" | "close";

export function SalesOrderDetailPage() {
  const { orderId } = useParams({ strict: false }) as { orderId: string };
  const order = useSalesOrder(orderId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [modal, setModal] = useState<"dispatch" | "invoice" | "reject" | "cancel" | null>(null);
  const refresh = (data?: Order) => {
    if (data) queryClient.setQueryData(salesOrderKey(orderId), data);
    else queryClient.invalidateQueries({ queryKey: salesOrderKey(orderId) });
    for (const key of ["sales-orders", "orders-received", "dispatches", "sales-invoices", "stock", "items"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
  const path = { params: { path: { order_id: Number(orderId) } } };
  const act = useMutation({
    mutationFn: async (action: Action) => {
      switch (action) {
        case "submit": return unwrap(await api.POST("/api/v1/sales-orders/{order_id}/submit", path));
        case "approve": return unwrap(await api.POST("/api/v1/sales-orders/{order_id}/approve", path));
        case "close": return unwrap(await api.POST("/api/v1/sales-orders/{order_id}/close", path));
      }
    },
    onSuccess: (data) => refresh(data),
  });
  const cancelPlain = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/sales-orders/{order_id}/cancel", { ...path, body: { reason: "" } })),
    onSuccess: (data) => refresh(data),
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/sales-orders/{order_id}", path)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
      navigate({ to: "/sales/orders" });
    },
  });

  if (order.isLoading) return <Loading />;
  if (order.error || !order.data) return <ErrorText error={order.error} />;
  const o = order.data;
  const a = o.actions;

  return (
    <div className="space-y-6">
      <PageHeader
        title={o.number}
        description={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={o.status} label={o.status_label} /> {o.customer.name} · {day(o.order_date)}{o.from_purchase_order ? <Badge tone="navy">From their PO {o.customer_po_number}</Badge> : null}</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            {a.edit ? <Link to="/sales/orders/$orderId/edit" params={{ orderId }} className="btn-secondary">Edit</Link> : null}
            {a.edit ? <button type="button" className="btn-secondary" onClick={() => { if (window.confirm("Delete this draft?")) remove.mutate(); }}>Delete</button> : null}
            {a.submit ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("submit")}>{o.submit_needs_approval ? "Send for approval" : "Confirm order"}</button> : null}
            {a.approve ? <button type="button" className="btn-secondary" onClick={() => setModal("reject")}>Send back</button> : null}
            {a.approve ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("approve")}>Approve</button> : null}
            {a.dispatch ? <button type="button" className="btn-primary" onClick={() => setModal("dispatch")}>Dispatch goods</button> : null}
            {a.invoice ? <button type="button" className="btn-secondary" onClick={() => setModal("invoice")}>Raise invoice</button> : null}
            {a.close ? <button type="button" className="btn-secondary" onClick={() => { if (window.confirm("Close this order? Nothing more will be sent against it.")) act.mutate("close"); }}>Close</button> : null}
            {a.cancel ? (
              <button type="button" className="btn-danger" onClick={() => {
                if (o.cancel_needs_reason) setModal("cancel");
                else if (window.confirm("Cancel this order?")) cancelPlain.mutate();
              }}>Cancel order</button>
            ) : null}
          </div>
        }
      />
      <ErrorText error={act.error ?? remove.error ?? cancelPlain.error} />
      {o.status === "draft" && o.rejection_reason ? <Notice tone="amber">Sent back: {o.rejection_reason}</Notice> : null}
      {o.status === "pending_approval" ? <Notice tone="amber">Waiting for someone who can approve sales orders.</Notice> : null}

      <Card>
        <dl className="grid gap-4 text-sm sm:grid-cols-4">
          <div><dt className="text-gray-500">Customer</dt><dd className="font-semibold">{o.customer.name}</dd><dd className="text-xs text-gray-500">{o.customer.gstin}</dd></div>
          <div><dt className="text-gray-500">Ship to</dt><dd className="whitespace-pre-line">{o.ship_to || "—"}</dd></div>
          <div><dt className="text-gray-500">Dispatch from</dt><dd>{o.dispatch_from}</dd></div>
          <div><dt className="text-gray-500">Deliver by · terms</dt><dd>{day(o.delivery_date)} · {o.payment_terms_days != null ? `${o.payment_terms_days} days` : "—"}</dd></div>
        </dl>
        {o.notes ? <p className="mt-4 whitespace-pre-line text-sm text-gray-700">{o.notes}</p> : null}
      </Card>

      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head text-right">Ordered</th>
              <th scope="col" className="table-head text-right">Dispatched</th>
              <th scope="col" className="table-head text-right">Invoiced</th>
              <th scope="col" className="table-head text-right">Rate · GST</th>
              <th scope="col" className="table-head text-right">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {o.lines.map((l) => (
              <tr key={l.id}>
                <td className="table-cell">
                  <p className="font-semibold text-navy-900">{l.item_name}</p>
                  <p className="text-xs text-gray-500">{l.item_code} · HSN {l.hsn_code || "—"}{l.customer_item ? ` · their item ${l.customer_item}` : ""}</p>
                  {l.on_hand != null && Number(l.on_hand) < Number(l.quantity) - Number(l.dispatched_quantity) ? (
                    <p className="text-xs text-amber-700">{quantity(l.on_hand, l.unit)} in {o.dispatch_from}</p>
                  ) : null}
                </td>
                <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
                <td className="table-cell text-right text-sm">{quantity(l.dispatched_quantity)}</td>
                <td className="table-cell text-right text-sm">{quantity(l.invoiced_quantity)}</td>
                <td className="table-cell text-right text-sm">{rupees(l.rate)} · {Number(l.gst_rate)}%</td>
                <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-4"><TotalsTable totals={o} /></div>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Dispatches">
          {o.dispatches.length === 0 ? <p className="text-sm text-gray-500">Nothing sent yet.</p> : (
            <ul className="divide-y divide-gray-100">
              {o.dispatches.map((d) => (
                <li key={d.id} className="py-2 text-sm"><span className="font-semibold">{d.number}</span><p className="text-xs text-gray-500">{day(d.date)} · {d.detail}</p></li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Invoices">
          {o.invoices.length === 0 ? <p className="text-sm text-gray-500">No invoices yet.</p> : (
            <ul className="divide-y divide-gray-100">
              {o.invoices.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div>
                    <Link to="/sales/invoices" search={{ invoice: i.id }} className="font-semibold text-navy-900 hover:underline">{i.number}</Link>
                    <p className="text-xs text-gray-500">{day(i.date)}{i.detail ? ` · ${i.detail}` : ""}</p>
                  </div>
                  <div className="flex items-center gap-2">{i.total != null ? rupees(i.total) : null}<StatusBadge status={i.status} label={i.status_label} /></div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {modal === "dispatch" ? <DispatchModal order={o} onClose={() => setModal(null)} onDone={() => { refresh(); setModal(null); }} /> : null}
      {modal === "invoice" ? <InvoiceModal order={o} onClose={() => setModal(null)} onDone={(id) => { refresh(); navigate({ to: "/sales/invoices", search: { invoice: id } }); }} /> : null}
      {modal === "reject" ? (
        <ReasonModal title="Send the order back" label="What needs to change?" button="Send back" onClose={() => setModal(null)} send={async (reason) => {
          refresh(unwrap(await api.POST("/api/v1/sales-orders/{order_id}/reject", { ...path, body: { reason } })));
          setModal(null);
        }} />
      ) : null}
      {modal === "cancel" ? (
        <ReasonModal title={`Cancel ${o.number}`} label={`Why? ${o.customer.name} will see this on their purchase order.`} button="Cancel order" onClose={() => setModal(null)} send={async (reason) => {
          refresh(unwrap(await api.POST("/api/v1/sales-orders/{order_id}/cancel", { ...path, body: { reason } })));
          setModal(null);
        }} />
      ) : null}
    </div>
  );
}

function DispatchModal({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: () => void }) {
  const warehouses = useWarehouses();
  const open = order.lines.map((l) => ({ line: l, open: Number(l.quantity) - Number(l.dispatched_quantity) })).filter((x) => x.open > 0);
  const [quantities, setQuantities] = useState<Record<number, string>>(
    Object.fromEntries(open.map((x) => [x.line.id, String(x.line.on_hand != null ? Math.max(0, Math.min(x.open, Number(x.line.on_hand))) : x.open)])),
  );
  const [warehouseId, setWarehouseId] = useState(order.dispatch_from_id);
  const [sent, setSent] = useState(todayIso());
  const [vehicle, setVehicle] = useState("");
  const [transporter, setTransporter] = useState("");
  const post = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/sales-orders/{order_id}/dispatches", {
      params: { path: { order_id: order.id } },
      body: { warehouse_id: warehouseId, dispatch_date: sent, vehicle_number: vehicle, transporter, notes: "",
              lines: open.map((x) => ({ order_line_id: x.line.id, quantity: quantities[x.line.id] || "0" })) },
    })),
    onSuccess: onDone,
  });
  return (
    <Modal wide title={`Dispatch against ${order.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={post.isPending} onClick={() => post.mutate()}>Post dispatch</button>
      </>
    }>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="From warehouse">
          {(id) => (
            <select id={id} className="field-input" value={warehouseId} onChange={(e) => setWarehouseId(Number(e.target.value))}>
              {(warehouses.data?.warehouses ?? []).filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Dispatched on">{(id) => <input id={id} type="date" className="field-input" value={sent} max={todayIso()} onChange={(e) => setSent(e.target.value)} />}</Field>
        <Field label="Vehicle number">{(id) => <input id={id} className="field-input uppercase" value={vehicle} onChange={(e) => setVehicle(e.target.value)} maxLength={20} />}</Field>
        <Field label="Transporter">{(id) => <input id={id} className="field-input" value={transporter} onChange={(e) => setTransporter(e.target.value)} maxLength={100} />}</Field>
      </div>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Still to send</th><th scope="col" className="table-head text-right">In stock</th><th scope="col" className="table-head w-36">Sending now</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {open.map((x) => (
            <tr key={x.line.id}>
              <td className="table-cell text-sm">{x.line.item_name}</td>
              <td className="table-cell text-right text-sm">{quantity(x.open, x.line.unit)}</td>
              <td className="table-cell text-right text-sm">{x.line.on_hand != null ? quantity(x.line.on_hand) : "—"}</td>
              <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Sending now, ${x.line.item_name}`} value={quantities[x.line.id]} onChange={(e) => setQuantities({ ...quantities, [x.line.id]: e.target.value })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-500">"In stock" is for {order.dispatch_from}. Goods leave stock at their average cost.</p>
      <ErrorText error={post.error} />
    </Modal>
  );
}

function InvoiceModal({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (invoiceId: number) => void }) {
  const ready = order.lines.filter((l) => Number(l.invoiceable_quantity) > 0);
  const [quantities, setQuantities] = useState<Record<number, string>>(Object.fromEntries(ready.map((l) => [l.id, String(Number(l.invoiceable_quantity))])));
  const [invoiceDate, setInvoiceDate] = useState(todayIso());
  const save = useMutation({
    mutationFn: async (post: boolean) => {
      const invoice = unwrap(await api.POST("/api/v1/sales-invoices", {
        body: { order_id: order.id, invoice_date: invoiceDate, notes: "", lines: ready.map((l) => ({ order_line_id: l.id, quantity: quantities[l.id] || "0" })) },
      }));
      return post ? unwrap(await api.POST("/api/v1/sales-invoices/{invoice_id}/post", { params: { path: { invoice_id: invoice.id } } })) : invoice;
    },
    onSuccess: (invoice) => onDone(invoice.id),
  });
  return (
    <Modal wide title={`Invoice ${order.customer.name} for ${order.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-secondary" disabled={save.isPending} onClick={() => save.mutate(false)}>Save draft</button>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(true)}>Post invoice</button>
      </>
    }>
      <Field label="Invoice date">{(id) => <input id={id} type="date" className="field-input max-w-xs" value={invoiceDate} max={todayIso()} onChange={(e) => setInvoiceDate(e.target.value)} />}</Field>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Dispatched, not invoiced</th><th scope="col" className="table-head text-right">Rate</th><th scope="col" className="table-head w-36">Invoice now</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {ready.map((l) => (
            <tr key={l.id}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">HSN {l.hsn_code || "—"}</span></td>
              <td className="table-cell text-right text-sm">{quantity(l.invoiceable_quantity, l.unit)}</td>
              <td className="table-cell text-right text-sm">{rupees(l.rate)} + {Number(l.gst_rate)}%</td>
              <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Invoice now, ${l.item_name}`} value={quantities[l.id]} onChange={(e) => setQuantities({ ...quantities, [l.id]: e.target.value })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-500">
        {order.is_interstate ? "IGST: the customer is in another state." : "CGST + SGST: the customer is in your state."} The invoice number is given when you post it, so drafts never leave gaps in the series.
      </p>
      <ErrorText error={save.error} />
    </Modal>
  );
}
