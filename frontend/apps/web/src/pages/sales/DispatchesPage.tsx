import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";
import { day, quantity } from "../../lib/format";

export function DispatchesPage() {
  const dispatches = useQuery({ queryKey: ["dispatches"], queryFn: async () => unwrap(await api.GET("/api/v1/dispatches")) });
  return (
    <div className="space-y-6">
      <PageHeader title="Dispatches" description="Goods sent to customers (delivery challans). Each one took its goods out of stock. Dispatch from the sales order." />
      <Card className="overflow-x-auto p-0">
        {dispatches.isLoading ? (
          <Loading />
        ) : dispatches.error ? (
          <div className="p-5"><ErrorText error={dispatches.error} /></div>
        ) : (dispatches.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="Nothing dispatched yet" /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Challan</th>
                <th scope="col" className="table-head">Order · customer</th>
                <th scope="col" className="table-head">Items</th>
                <th scope="col" className="table-head">From · vehicle</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(dispatches.data ?? []).map((d) => (
                <tr key={d.id}>
                  <td className="table-cell"><p className="font-semibold text-navy-900">{d.number}</p><p className="text-xs text-gray-500">{day(d.dispatch_date)}{d.dispatched_by ? ` · ${d.dispatched_by}` : ""}</p></td>
                  <td className="table-cell text-sm">
                    <Link to="/sales/orders/$orderId" params={{ orderId: String(d.order_id) }} className="font-semibold hover:underline">{d.order_number}</Link>
                    <p className="text-xs text-gray-500">{d.customer.name}</p>
                  </td>
                  <td className="table-cell text-sm">{d.lines.map((l) => <p key={l.item_code}>{quantity(l.quantity, l.unit)} {l.item_name}</p>)}</td>
                  <td className="table-cell text-sm">{d.warehouse}<p className="text-xs text-gray-500">{[d.vehicle_number, d.transporter].filter(Boolean).join(" · ") || "—"}</p></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
