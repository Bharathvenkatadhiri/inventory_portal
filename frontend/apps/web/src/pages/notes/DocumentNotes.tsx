import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { day, rupees } from "../../lib/format";
import { StatusBadge } from "../purchase/PurchaseLayout";
import type { Side } from "./NoteForm";

/** The credit and debit notes against one invoice or bill, and the total after them. */
export function DocumentNotes({ side, documentId, total, adjustedTotal }: {
  side: Side; documentId: number; total: string | number; adjustedTotal: string | number;
}) {
  const notes = useQuery({
    queryKey: ["notes", side, "document", documentId],
    queryFn: async () => unwrap(await api.GET("/api/v1/notes", { params: { query: { side, document_id: documentId } } })),
  });
  const list = notes.data ?? [];
  if (list.length === 0) return null;
  return (
    <div className="rounded-lg border border-gray-200 p-3 text-sm">
      <p className="field-label">{side === "sales" ? "Credit and debit notes" : "Supplier's notes"}</p>
      <ul className="divide-y divide-gray-100">
        {list.map((n) => (
          <li key={n.id} className="flex items-center justify-between gap-3 py-1">
            <span>
              <Link to={side === "sales" ? "/sales/notes" : "/purchase/notes"} search={{ note: n.id }} className="font-semibold hover:underline">
                {n.number || "Draft"}
              </Link>{" "}
              <span className="text-gray-500">{n.kind_label} · {n.reason_label} · {day(n.note_date)}</span>
            </span>
            <span className="flex items-center gap-2">
              <span className={n.kind === "credit" ? "text-red-700" : ""}>{n.kind === "credit" ? "−" : "+"}{rupees(n.total)}</span>
              <StatusBadge status={n.status} label={n.status_label} />
            </span>
          </li>
        ))}
      </ul>
      {Number(adjustedTotal) !== Number(total) ? (
        <p className="mt-2">Total after posted notes: <strong>{rupees(adjustedTotal)}</strong> <span className="text-gray-500">(was {rupees(total)})</span></p>
      ) : null}
    </div>
  );
}
