import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";

import { Badge, ErrorText, Loading } from "../../components/ui";
import { quantity } from "../../lib/format";

/** A BOM exploded to every level for a quantity: made sub-assemblies indented under themselves. */
export function BomTree({ bomId, quantity: qty, warehouseId }: { bomId: number; quantity: string; warehouseId?: number | null }) {
  const tree = useQuery({
    queryKey: ["bom-explode", bomId, qty, warehouseId],
    queryFn: async () => unwrap(await api.GET("/api/v1/boms/{bom_id}/explode", {
      params: { path: { bom_id: bomId }, query: { quantity: qty, ...(warehouseId ? { warehouse_id: warehouseId } : {}) } },
    })),
  });
  if (tree.isLoading) return <Loading />;
  if (tree.error) return <ErrorText error={tree.error} />;
  const withStock = (tree.data ?? []).some((r) => r.on_hand !== null && r.on_hand !== undefined);
  return (
    <table className="w-full">
      <thead className="border-b border-gray-100">
        <tr>
          <th scope="col" className="table-head">Component</th>
          <th scope="col" className="table-head text-right">Needed</th>
          {withStock ? <th scope="col" className="table-head text-right">In stock</th> : null}
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {(tree.data ?? []).map((r, i) => (
          <tr key={i}>
            <td className="table-cell text-sm" style={{ paddingLeft: `${(r.level - 1) * 1.25 + 0.5}rem` }}>
              {r.level > 1 ? <span className="text-gray-400" aria-hidden="true">└ </span> : null}
              {r.item_name} <span className="text-gray-500">{r.item_code}</span> {r.made ? <Badge tone="navy">Made</Badge> : null}
            </td>
            <td className="table-cell text-right text-sm">{quantity(r.quantity, r.unit)}</td>
            {withStock ? (
              <td className={`table-cell text-right text-sm ${Number(r.short) > 0 ? "font-semibold text-red-700" : ""}`}>
                {quantity(r.on_hand)}{Number(r.short) > 0 ? ` (short ${quantity(r.short)})` : ""}
              </td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
