import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Card, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { day, quantity, rupees } from "../../lib/format";
import { useWarehouses } from "../inventory/InventoryLayout";
import { orderKey, StatusBadge, todayIso, TotalsTable, useOrder } from "./PurchaseLayout";

type Order = Schemas["OrderOut"];
type Action = "submit" | "approve" | "cancel" | "close";

export function OrderDetailPage() {
  const { orderId } = useParams({ strict: false }) as { orderId: string };
  const order = useOrder(orderId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [modal, setModal] = useState<"receive" | "bill" | "reject" | null>(null);
  const refresh = (data?: Order) => {
    if (data) queryClient.setQueryData(orderKey(orderId), data);
    else queryClient.invalidateQueries({ queryKey: orderKey(orderId) });
    queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    queryClient.invalidateQueries({ queryKey: ["purchase-bills"] });
    queryClient.invalidateQueries({ queryKey: ["goods-receipts"] });
    queryClient.invalidateQueries({ queryKey: ["stock"] });
    queryClient.invalidateQueries({ queryKey: ["items"] });
  };
  const act = useMutation({
    mutationFn: async (action: Action) => {
      const path = { params: { path: { order_id: Number(orderId) } } };
      switch (action) {
        case "submit": return unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/submit", path));
        case "approve": return unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/approve", path));
        case "cancel": return unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/cancel", path));
        case "close": return unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/close", path));
      }
    },
    onSuccess: (data) => refresh(data),
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/purchase-orders/{order_id}", { params: { path: { order_id: Number(orderId) } } })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      navigate({ to: "/purchase/orders" });
    },
  });

  if (order.isLoading) return <Loading />;
  if (order.error || !order.data) return <ErrorText error={order.error} />;
  const o = order.data;
  const a = o.actions;
  const confirmThen = (message: string, action: Action) => () => { if (window.confirm(message)) act.mutate(action); };

  return (
    <div className="space-y-6">
      <PageHeader
        title={o.number}
        description={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={o.status} label={o.status_label} /> {o.supplier.name} · {day(o.order_date)}</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <PdfButton url={`/api/v1/purchase-orders/${o.id}/pdf`} label={o.status === "draft" || o.status === "pending_approval" ? "Preview PDF" : "PDF"} />
            {a.edit ? <Link to="/purchase/orders/$orderId/edit" params={{ orderId }} className="btn-secondary">Edit</Link> : null}
            {a.edit ? <button type="button" className="btn-secondary" onClick={() => { if (window.confirm("Delete this draft?")) remove.mutate(); }}>Delete</button> : null}
            {a.submit ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("submit")}>{o.submit_needs_approval ? "Send for approval" : "Issue to supplier"}</button> : null}
            {a.approve ? <button type="button" className="btn-secondary" onClick={() => setModal("reject")}>Send back</button> : null}
            {a.approve ? <button type="button" className="btn-primary" disabled={act.isPending} onClick={() => act.mutate("approve")}>Approve and issue</button> : null}
            {a.receive ? <button type="button" className="btn-primary" onClick={() => setModal("receive")}>Receive goods</button> : null}
            {a.bill ? <button type="button" className="btn-secondary" onClick={() => setModal("bill")}>Enter bill</button> : null}
            {a.close ? <button type="button" className="btn-secondary" onClick={confirmThen("Close this order? Nothing more will be received against it.", "close")}>Close</button> : null}
            {a.cancel ? <button type="button" className="btn-danger" onClick={confirmThen("Cancel this order?", "cancel")}>Cancel order</button> : null}
          </div>
        }
      />
      <ErrorText error={act.error ?? remove.error} />
      {o.status === "draft" && o.rejection_reason ? <Notice tone="amber">Sent back: {o.rejection_reason}</Notice> : null}
      {o.status === "pending_approval" ? <Notice tone="amber">Waiting for someone who can approve purchase orders.</Notice> : null}
      <SupplierReply order={o} />

      <Card>
        <dl className="grid gap-4 text-sm sm:grid-cols-4">
          <div><dt className="text-gray-500">Supplier</dt><dd className="font-semibold">{o.supplier.name}</dd><dd className="text-xs text-gray-500">{o.supplier.gstin}</dd></div>
          <div><dt className="text-gray-500">Deliver to</dt><dd>{o.ship_to}</dd></div>
          <div><dt className="text-gray-500">Expected by</dt><dd>{day(o.expected_date)}</dd></div>
          <div><dt className="text-gray-500">Payment terms</dt><dd>{o.payment_terms_days != null ? `${o.payment_terms_days} days` : "—"}</dd></div>
          {o.supplier_reference ? <div><dt className="text-gray-500">Supplier's reference</dt><dd>{o.supplier_reference}</dd></div> : null}
          <div><dt className="text-gray-500">Raised by</dt><dd>{o.created_by || "—"}</dd></div>
          {o.approved_by ? <div><dt className="text-gray-500">Issued by</dt><dd>{o.approved_by}</dd></div> : null}
        </dl>
        {o.notes ? <p className="mt-4 whitespace-pre-line text-sm text-gray-700">{o.notes}</p> : null}
      </Card>

      <Card title="Items" className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head text-right">Ordered</th>
              <th scope="col" className="table-head text-right">Received</th>
              <th scope="col" className="table-head text-right">Billed</th>
              <th scope="col" className="table-head text-right">Rate</th>
              <th scope="col" className="table-head text-right">GST</th>
              <th scope="col" className="table-head text-right">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {o.lines.map((l) => (
              <tr key={l.id}>
                <td className="table-cell"><p className="font-semibold text-navy-900">{l.item_name}</p><p className="text-xs text-gray-500">{l.item_code}{l.description ? ` · ${l.description}` : ""}</p></td>
                <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
                <td className="table-cell text-right text-sm">{quantity(l.received_quantity)}</td>
                <td className="table-cell text-right text-sm">{quantity(l.billed_quantity)}</td>
                <td className="table-cell text-right text-sm">{rupees(l.rate)}</td>
                <td className="table-cell text-right text-sm">{Number(l.gst_rate)}%</td>
                <td className="table-cell text-right text-sm">{rupees(l.taxable_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-4"><TotalsTable totals={o} /></div>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <DocList title="Goods receipts" empty="Nothing received yet." docs={o.receipts} />
        <DocList title="Bills" empty="No bills entered yet." docs={o.bills} link />
      </div>
      {o.supplier_response === "accepted" ? (
        <div className="grid gap-6 md:grid-cols-2">
          <DocList title={`Sent by ${o.supplier.name}`} empty="Nothing dispatched yet." docs={o.supplier_dispatches} pdf={(id) => `/api/v1/dispatches/${id}/pdf`} />
          <DocList title={`Invoiced by ${o.supplier.name}`} empty="No invoices yet." docs={o.supplier_invoices} pdf={(id) => `/api/v1/sales-invoices/${id}/pdf`} />
        </div>
      ) : null}

      {modal === "receive" ? <ReceiveModal order={o} onClose={() => setModal(null)} onDone={() => { refresh(); setModal(null); }} /> : null}
      {modal === "bill" ? <BillModal order={o} onClose={() => setModal(null)} onDone={() => { refresh(); setModal(null); }} /> : null}
      {modal === "reject" ? <RejectModal what="order" onClose={() => setModal(null)} send={async (reason) => {
        refresh(unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/reject", { params: { path: { order_id: o.id } }, body: { reason } })));
        setModal(null);
      }} /> : null}
    </div>
  );
}

function SupplierReply({ order: o }: { order: Order }) {
  const live = !["draft", "pending_approval", "cancelled"].includes(o.status);
  if (!live || !o.supplier_on_network) return null;
  if (o.supplier_response === "accepted") return <Notice tone="green">{o.supplier.name} accepted this order.</Notice>;
  if (o.supplier_response === "declined") {
    return <Notice tone="red">{o.supplier.name} declined this order{o.decline_reason ? `: ${o.decline_reason}` : "."} Cancel it, or raise a new one.</Notice>;
  }
  return <Notice tone="navy">Waiting for {o.supplier.name} to accept the order on MakeSetu.</Notice>;
}

function DocList({ title, empty, docs, link = false, pdf }: {
  title: string; empty: string; docs: Schemas["DocRefOut"][]; link?: boolean; pdf?: (id: number) => string;
}) {
  return (
    <Card title={title}>
      {docs.length === 0 ? <p className="text-sm text-gray-500">{empty}</p> : (
        <ul className="divide-y divide-gray-100">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <div>
                {link ? <Link to="/purchase/bills" search={{ bill: d.id }} className="font-semibold text-navy-900 hover:underline">{d.number}</Link> : <span className="font-semibold">{d.number}</span>}
                <p className="text-xs text-gray-500">{day(d.date)} · {d.detail}</p>
              </div>
              <div className="flex items-center gap-2">
                {d.total != null ? <span>{rupees(d.total)}</span> : null}
                {d.status !== "posted" || link ? <StatusBadge status={d.status} label={d.status_label} /> : null}
                {pdf ? <PdfButton url={pdf(d.id)} className="btn-link" /> : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ReceiveModal({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: () => void }) {
  const warehouses = useWarehouses();
  const open = order.lines.map((l) => ({ line: l, open: Number(l.quantity) - Number(l.received_quantity) })).filter((x) => x.open > 0);
  const [quantities, setQuantities] = useState<Record<number, string>>(Object.fromEntries(open.map((x) => [x.line.id, String(x.open)])));
  const [warehouseId, setWarehouseId] = useState<number>(order.ship_to_id);
  const [received, setReceived] = useState(todayIso());
  const [challan, setChallan] = useState("");
  const [vehicle, setVehicle] = useState("");
  const post = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/purchase-orders/{order_id}/receipts", {
      params: { path: { order_id: order.id } },
      body: {
        warehouse_id: warehouseId, received_date: received, supplier_challan: challan, vehicle_number: vehicle, notes: "",
        lines: open.map((x) => ({ order_line_id: x.line.id, quantity: quantities[x.line.id] || "0" })),
      },
    })),
    onSuccess: onDone,
  });
  return (
    <Modal wide title={`Receive goods against ${order.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={post.isPending} onClick={() => post.mutate()}>Post receipt</button>
      </>
    }>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Into warehouse">
          {(id) => (
            <select id={id} className="field-input" value={warehouseId} onChange={(e) => setWarehouseId(Number(e.target.value))}>
              {(warehouses.data?.warehouses ?? []).filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Received on">{(id) => <input id={id} type="date" className="field-input" value={received} max={todayIso()} onChange={(e) => setReceived(e.target.value)} />}</Field>
        <Field label="Supplier's challan / invoice no.">{(id) => <input id={id} className="field-input" value={challan} onChange={(e) => setChallan(e.target.value)} maxLength={60} />}</Field>
        <Field label="Vehicle number">{(id) => <input id={id} className="field-input uppercase" value={vehicle} onChange={(e) => setVehicle(e.target.value)} maxLength={20} />}</Field>
      </div>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Still to come</th><th scope="col" className="table-head w-40">Received now</th></tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {open.map((x) => (
            <tr key={x.line.id}>
              <td className="table-cell text-sm">{x.line.item_name}</td>
              <td className="table-cell text-right text-sm">{quantity(x.open, x.line.unit)}</td>
              <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Received now, ${x.line.item_name}`} value={quantities[x.line.id]} onChange={(e) => setQuantities({ ...quantities, [x.line.id]: e.target.value })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-500">Goods go into stock at the order's rates. Enter 0 for anything that didn't come.</p>
      <ErrorText error={post.error} />
    </Modal>
  );
}

function BillModal({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: () => void }) {
  const billable = order.lines.filter((l) => Number(l.billable_quantity) > 0);
  const [rows, setRows] = useState<Record<number, { quantity: string; rate: string }>>(
    Object.fromEntries(billable.map((l) => [l.id, { quantity: String(Number(l.billable_quantity)), rate: String(Number(l.rate)) }])),
  );
  const [invoice, setInvoice] = useState("");
  const [invoiceDate, setInvoiceDate] = useState(todayIso());
  const save = useMutation({
    mutationFn: async (submit: boolean) => {
      const bill = unwrap(await api.POST("/api/v1/purchase-bills", {
        body: {
          order_id: order.id, supplier_invoice_number: invoice, invoice_date: invoiceDate, notes: "",
          lines: billable.map((l) => ({ order_line_id: l.id, quantity: rows[l.id].quantity || "0", rate: rows[l.id].rate || null, gst_rate: null })),
        },
      }));
      return submit ? unwrap(await api.POST("/api/v1/purchase-bills/{bill_id}/submit", { params: { path: { bill_id: bill.id } } })) : bill;
    },
    onSuccess: onDone,
  });
  return (
    <Modal wide title={`Enter the supplier's bill for ${order.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-secondary" disabled={!invoice.trim() || save.isPending} onClick={() => save.mutate(false)}>Save draft</button>
        <button type="button" className="btn-primary" disabled={!invoice.trim() || save.isPending} onClick={() => save.mutate(true)}>Save and post</button>
      </>
    }>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Supplier's invoice number">{(id) => <input id={id} className="field-input uppercase" value={invoice} onChange={(e) => setInvoice(e.target.value)} maxLength={30} />}</Field>
        <Field label="Invoice date">{(id) => <input id={id} type="date" className="field-input" value={invoiceDate} max={todayIso()} onChange={(e) => setInvoiceDate(e.target.value)} />}</Field>
      </div>
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr>
            <th scope="col" className="table-head">Item</th>
            <th scope="col" className="table-head text-right">Received, not billed</th>
            <th scope="col" className="table-head w-32">Quantity</th>
            <th scope="col" className="table-head w-32">Rate (₹)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {billable.map((l) => {
            const differs = rows[l.id].rate !== "" && Number(rows[l.id].rate) !== Number(l.rate);
            return (
              <tr key={l.id}>
                <td className="table-cell text-sm">{l.item_name}</td>
                <td className="table-cell text-right text-sm">{quantity(l.billable_quantity, l.unit)}</td>
                <td className="table-cell"><input className="field-input" inputMode="decimal" aria-label={`Quantity billed, ${l.item_name}`} value={rows[l.id].quantity} onChange={(e) => setRows({ ...rows, [l.id]: { ...rows[l.id], quantity: e.target.value } })} /></td>
                <td className="table-cell">
                  <input className="field-input" inputMode="decimal" aria-label={`Rate billed, ${l.item_name}`} value={rows[l.id].rate} onChange={(e) => setRows({ ...rows, [l.id]: { ...rows[l.id], rate: e.target.value } })} />
                  {differs ? <p className="mt-1 text-xs text-amber-700">Order rate {rupees(l.rate)}</p> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-xs text-gray-500">GST is worked out at the order's rates; {order.is_interstate ? "IGST, as the supplier is in another state" : "CGST + SGST, as the supplier is in the same state"}.</p>
      <ErrorText error={save.error} />
    </Modal>
  );
}

export function RejectModal({ what, onClose, send }: { what: string; onClose: () => void; send: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  const run = useMutation({ mutationFn: () => send(reason) });
  return (
    <Modal title={`Send the ${what} back`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!reason.trim() || run.isPending} onClick={() => run.mutate()}>Send back</button>
      </>
    }>
      <Field label="What needs to change?">{(id) => <textarea id={id} className="field-input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />}</Field>
      <ErrorText error={run.error} />
    </Modal>
  );
}
