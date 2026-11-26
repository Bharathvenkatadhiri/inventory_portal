import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";
import { day, quantity } from "../../lib/format";

export function ReceiptsPage() {
  const receipts = useQuery({
    queryKey: ["goods-receipts"],
    queryFn: async () => unwrap(await api.GET("/api/v1/goods-receipts")),
  });
  return (
    <div className="space-y-6">
      <PageHeader title="Goods receipts" description="Everything received against purchase orders (GRNs). Each one added its goods to stock. Receive goods from the purchase order." />
      <Card className="overflow-x-auto p-0">
        {receipts.isLoading ? (
          <Loading />
        ) : receipts.error ? (
          <div className="p-5"><ErrorText error={receipts.error} /></div>
        ) : (receipts.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="Nothing received yet" /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Receipt</th>
                <th scope="col" className="table-head">Order · supplier</th>
                <th scope="col" className="table-head">Items</th>
                <th scope="col" className="table-head">Into</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(receipts.data ?? []).map((r) => (
                <tr key={r.id}>
                  <td className="table-cell">
                    <p className="font-semibold text-navy-900">{r.number}</p>
                    <p className="text-xs text-gray-500">{day(r.received_date)}{r.received_by ? ` · ${r.received_by}` : ""}</p>
                  </td>
                  <td className="table-cell text-sm">
                    <Link to="/purchase/orders/$orderId" params={{ orderId: String(r.order_id) }} className="font-semibold hover:underline">{r.order_number}</Link>
                    <p className="text-xs text-gray-500">{r.supplier.name}{r.supplier_challan ? ` · challan ${r.supplier_challan}` : ""}{r.vehicle_number ? ` · ${r.vehicle_number}` : ""}</p>
                  </td>
                  <td className="table-cell text-sm">
                    {r.lines.map((l) => <p key={l.item_code}>{quantity(l.quantity, l.unit)} {l.item_name}</p>)}
                  </td>
                  <td className="table-cell text-sm">{r.warehouse}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
