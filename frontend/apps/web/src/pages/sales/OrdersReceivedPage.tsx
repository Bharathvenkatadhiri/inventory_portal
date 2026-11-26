import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { day, quantity, rupees } from "../../lib/format";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { ReasonModal, StatusBadge, TotalsTable } from "../purchase/PurchaseLayout";

type Received = Schemas["ReceivedOrderOut"];

/** Purchase orders customers on MakeSetu have issued to this organization:
 * the same documents they see under Purchase. Accept one to make it a sales
 * order, or decline it with a reason they'll see. */
export function OrdersReceivedPage() {
  const queryClient = useQueryClient();
  const orders = useQuery({
    queryKey: ["orders-received"],
    queryFn: async () => unwrap(await api.GET("/api/v1/sales/orders-received")),
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const [replying, setReplying] = useState<{ order: Received; mode: "accept" | "decline" } | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["orders-received"] });
    queryClient.invalidateQueries({ queryKey: ["sales-orders"] });
  };
  const awaiting = (orders.data ?? []).filter((o) => o.can_reply).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Orders received"
        description="Purchase orders your customers on MakeSetu have issued to you. Accept one to turn it into a sales order at its quantities and rates, or decline it; the customer sees your reply."
      />
      {awaiting ? <Notice tone="amber">{awaiting === 1 ? "1 order is" : `${awaiting} orders are`} waiting for your reply.</Notice> : null}
      {orders.isLoading ? (
        <Loading />
      ) : orders.error ? (
        <ErrorText error={orders.error} />
      ) : (orders.data ?? []).length === 0 ? (
        <Empty title="No orders yet">When a customer on MakeSetu issues you a purchase order, it appears here.</Empty>
      ) : (
        <div className="space-y-4">
          {(orders.data ?? []).map((o) => {
            const open = openId === o.id;
            return (
              <Card key={o.id}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <button type="button" className="text-left" aria-expanded={open} onClick={() => setOpenId(open ? null : o.id)}>
                    <p className="font-semibold text-navy-900">{o.number} · {o.buyer.name}</p>
                    <p className="text-xs text-gray-500">{day(o.order_date)}{o.expected_date ? ` · wanted by ${day(o.expected_date)}` : ""} · {rupees(o.total)}</p>
                  </button>
                  <div className="flex flex-wrap items-center gap-2">
                    <PdfButton url={`/api/v1/purchase-orders/${o.id}/pdf`} label="PO PDF" className="btn-link" />
                    <StatusBadge status={o.supplier_response || "awaiting"} label={o.supplier_response_label} />
                    {o.sales_order_id ? (
                      <Link to="/sales/orders/$orderId" params={{ orderId: String(o.sales_order_id) }} className="btn-link">{o.sales_order_number}</Link>
                    ) : null}
                    {o.can_reply ? (
                      <>
                        <button type="button" className="btn-secondary" onClick={() => setReplying({ order: o, mode: "decline" })}>Decline</button>
                        <button type="button" className="btn-primary" onClick={() => setReplying({ order: o, mode: "accept" })}>Accept</button>
                      </>
                    ) : null}
                  </div>
                </div>
                {o.supplier_response === "declined" && o.decline_reason ? <p className="mt-2 text-sm text-gray-600">Declined: {o.decline_reason}</p> : null}
                {open ? <ReceivedDetail order={o} /> : null}
              </Card>
            );
          })}
        </div>
      )}
      {replying?.mode === "accept" ? <AcceptModal order={replying.order} onClose={() => setReplying(null)} onDone={refresh} /> : null}
      {replying?.mode === "decline" ? (
        <ReasonModal
          title={`Decline ${replying.order.number}`}
          label={`What should ${replying.order.buyer.name} know? (They'll see this.)`}
          button="Decline order"
          onClose={() => setReplying(null)}
          send={async (reason) => {
            unwrap(await api.POST("/api/v1/sales/orders-received/{po_id}/decline", { params: { path: { po_id: replying.order.id } }, body: { reason } }));
            refresh();
            setReplying(null);
          }}
        />
      ) : null}
    </div>
  );
}

function ReceivedDetail({ order: o }: { order: Received }) {
  return (
    <div className="mt-4 space-y-4 border-t border-gray-100 pt-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div><dt className="text-gray-500">Deliver to</dt><dd>{o.ship_to}{o.ship_to_address ? `, ${o.ship_to_address}` : ""}</dd></div>
        <div><dt className="text-gray-500">Customer GSTIN</dt><dd>{o.buyer.gstin || "—"}</dd></div>
        <div><dt className="text-gray-500">Payment terms</dt><dd>{o.payment_terms_days != null ? `${o.payment_terms_days} days` : "—"}</dd></div>
      </dl>
      {o.notes ? <p className="whitespace-pre-line text-sm text-gray-700">{o.notes}</p> : null}
      <table className="w-full">
        <thead className="border-b border-gray-100">
          <tr>
            <th scope="col" className="table-head">Item (customer's code)</th>
            <th scope="col" className="table-head text-right">Ordered</th>
            <th scope="col" className="table-head text-right">Received by them</th>
            <th scope="col" className="table-head text-right">Rate</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {o.lines.map((l) => (
            <tr key={l.id}>
              <td className="table-cell text-sm">{l.item_name} <span className="text-gray-500">{l.item_code}</span>{l.description ? <p className="text-xs text-gray-500">{l.description}</p> : null}</td>
              <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
              <td className="table-cell text-right text-sm">{quantity(l.received_quantity)}</td>
              <td className="table-cell text-right text-sm">{rupees(l.rate)} + {Number(l.gst_rate)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TotalsTable totals={o} />
    </div>
  );
}

function AcceptModal({ order, onClose, onDone }: { order: Received; onClose: () => void; onDone: () => void }) {
  const navigate = useNavigate();
  const items = useItems();
  const warehouses = useWarehouses();
  const activeWarehouses = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [mapping, setMapping] = useState<Record<number, number | null>>(
    Object.fromEntries(order.lines.map((l) => [l.id, (order.suggested_items as Record<string, number>)[String(l.id)] ?? null])),
  );
  const [fromId, setFromId] = useState<number | null>(null);
  const warehouseId = fromId ?? activeWarehouses.find((w) => w.is_default)?.id ?? activeWarehouses[0]?.id ?? null;
  const accept = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/sales/orders-received/{po_id}/accept", {
      params: { path: { po_id: order.id } },
      body: { dispatch_from_id: warehouseId!, lines: order.lines.map((l) => ({ purchase_order_line_id: l.id, item_id: mapping[l.id] })) },
    })),
    onSuccess: (result) => {
      onDone();
      navigate({ to: "/sales/orders/$orderId", params: { orderId: String(result.sales_order_id) } });
    },
  });
  const sellable = (items.data ?? []).filter((i) => i.is_active);
  const ready = warehouseId !== null && order.lines.every((l) => mapping[l.id]);

  return (
    <Modal wide title={`Accept ${order.number} from ${order.buyer.name}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={!ready || accept.isPending} onClick={() => accept.mutate()}>Accept and create sales order</button>
      </>
    }>
      <p className="text-sm text-gray-600">
        You'll supply these at the customer's quantities and rates ({rupees(order.taxable_value)} before GST). Match each line to your own item; GST is charged at your item's rate. We'll remember your choices for their next order.
      </p>
      {items.isLoading ? <Loading /> : (
        <table className="w-full">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">They ordered</th>
              <th scope="col" className="table-head">Your item</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {order.lines.map((l) => {
              const chosen = sellable.find((i) => i.id === mapping[l.id]);
              return (
                <tr key={l.id}>
                  <td className="table-cell text-sm">
                    <p className="font-semibold">{quantity(l.quantity, l.unit)} {l.item_name}</p>
                    <p className="text-xs text-gray-500">{l.item_code} · {rupees(l.rate)} each{l.description ? ` · ${l.description}` : ""}</p>
                  </td>
                  <td className="table-cell">
                    <select className="field-input" aria-label={`Your item for ${l.item_name}`} value={mapping[l.id] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, [l.id]: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Choose your item</option>
                      {sellable.filter((i) => i.unit === l.unit).map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                    </select>
                    {chosen && chosen.tracks_stock && chosen.on_hand != null && Number(chosen.on_hand) < Number(l.quantity) ? (
                      <p className="mt-1 text-xs text-amber-700">Only {quantity(chosen.on_hand, chosen.unit)} in stock across warehouses.</p>
                    ) : null}
                    {sellable.every((i) => i.unit !== l.unit) ? <p className="mt-1 text-xs text-gray-500">None of your items is kept in {l.unit}. Add one under Inventory.</p> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <Field label="Dispatch from">
        {(id) => (
          <select id={id} className="field-input" value={warehouseId ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
            {activeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
      </Field>
      <ErrorText error={accept.error} />
    </Modal>
  );
}
