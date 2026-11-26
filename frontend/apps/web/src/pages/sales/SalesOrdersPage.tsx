import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { StatusBadge } from "../purchase/PurchaseLayout";

const FILTERS = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "pending_approval", label: "Waiting for approval" },
  { key: "open", label: "Open (to dispatch)" },
  { key: "dispatched", label: "Dispatched" },
  { key: "closed", label: "Closed" },
  { key: "cancelled", label: "Cancelled" },
];

export function SalesOrdersPage() {
  const can = useCan();
  const writable = useWritable();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const orders = useQuery({
    queryKey: ["sales-orders", status, q],
    queryFn: async () => unwrap(await api.GET("/api/v1/sales-orders", { params: { query: { status, q } } })),
  });
  return (
    <div className="space-y-6">
      <PageHeader
        title="Sales orders"
        description="Orders you've accepted from customers on MakeSetu, and orders you enter yourself. Dispatch goods and raise GST invoices against them."
        actions={can("so.create") && writable ? <Link to="/sales/orders/new" className="btn-primary">New sales order</Link> : null}
      />
      <div className="flex flex-wrap gap-3">
        <input type="search" className="field-input max-w-xs" placeholder="Search number, customer or their PO" aria-label="Search sales orders"
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
          <div className="p-5"><Empty title={status || q ? "No orders match" : "No sales orders yet"}>{!status && !q ? "Accept an order received, or create one." : null}</Empty></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Order</th>
                <th scope="col" className="table-head">Customer</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head">Deliver by</th>
                <th scope="col" className="table-head text-right">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(orders.data ?? []).map((o) => (
                <tr key={o.id} className="hover:bg-gray-50">
                  <td className="table-cell">
                    <Link to="/sales/orders/$orderId" params={{ orderId: String(o.id) }} className="font-semibold text-navy-900 hover:underline">{o.number}</Link>
                    <p className="text-xs text-gray-500">{day(o.order_date)} · from {o.dispatch_from}</p>
                  </td>
                  <td className="table-cell text-sm">
                    {o.customer.name}
                    {o.customer_po_number ? <p className="text-xs text-gray-500">Their PO {o.customer_po_number}</p> : null}
                    {o.from_purchase_order ? <div className="mt-1"><Badge tone="navy">On MakeSetu</Badge></div> : null}
                  </td>
                  <td className="table-cell"><StatusBadge status={o.status} label={o.status_label} /></td>
                  <td className="table-cell text-sm">{day(o.delivery_date)}</td>
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
