import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { StatusBadge } from "./PurchaseLayout";

const FILTERS = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "pending_approval", label: "Waiting for approval" },
  { key: "open", label: "Open (to receive)" },
  { key: "received", label: "Received" },
  { key: "closed", label: "Closed" },
  { key: "cancelled", label: "Cancelled" },
];

export function OrdersPage() {
  const can = useCan();
  const writable = useWritable();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const orders = useQuery({
    queryKey: ["purchase-orders", status, q],
    queryFn: async () => unwrap(await api.GET("/api/v1/purchase-orders", { params: { query: { status, q } } })),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Purchase orders"
        description="Orders to your suppliers. Once issued, the supplier sees the same order on MakeSetu; goods received and bills are recorded against it."
        actions={can("po.create") && writable ? <Link to="/purchase/orders/new" className="btn-primary">New purchase order</Link> : null}
      />
      <div className="flex flex-wrap gap-3">
        <input type="search" className="field-input max-w-xs" placeholder="Search number or supplier" aria-label="Search orders"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="field-input max-w-[220px]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </div>
      <Card className="overflow-x-auto p-0">
        {orders.isLoading ? (
          <Loading />
        ) : orders.error ? (
          <div className="p-5"><ErrorText error={orders.error} /></div>
        ) : (orders.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title={status || q ? "No orders match" : "No purchase orders yet"} /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Order</th>
                <th scope="col" className="table-head">Supplier</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head">Expected</th>
                <th scope="col" className="table-head text-right">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(orders.data ?? []).map((o) => (
                <tr key={o.id} className="hover:bg-gray-50">
                  <td className="table-cell">
                    <Link to="/purchase/orders/$orderId" params={{ orderId: String(o.id) }} className="font-semibold text-navy-900 hover:underline">
                      {o.number}
                    </Link>
                    <p className="text-xs text-gray-500">{day(o.order_date)} · to {o.ship_to}</p>
                  </td>
                  <td className="table-cell text-sm">{o.supplier.name}</td>
                  <td className="table-cell">
                    <StatusBadge status={o.status} label={o.status_label} />
                    {o.supplier_on_network && ["issued", "partially_received"].includes(o.status) ? (
                      <div className="mt-1"><StatusBadge status={o.supplier_response || "awaiting"} label={o.supplier_response ? `Supplier ${o.supplier_response}` : "Awaiting supplier"} /></div>
                    ) : null}
                  </td>
                  <td className="table-cell text-sm">{day(o.expected_date)}</td>
                  <td className="table-cell text-right text-sm">{rupees(o.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
