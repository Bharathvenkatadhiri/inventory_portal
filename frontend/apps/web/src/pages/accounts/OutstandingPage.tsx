import { api, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { PAYMENT_TONES, type Direction } from "./AccountsLayout";
import { PaymentModal } from "./PaymentModal";

export function ReceivablesPage() {
  return <OutstandingPage direction="received" />;
}

export function PayablesPage() {
  return <OutstandingPage direction="made" />;
}

function OutstandingPage({ direction }: { direction: Direction }) {
  const can = useCan();
  const writable = useWritable();
  const received = direction === "received";
  const data = useQuery({
    queryKey: ["outstanding", direction, null],
    queryFn: async () => unwrap(await api.GET("/api/v1/accounts/outstanding", { params: { query: { direction } } })),
  });
  const [recording, setRecording] = useState<{ partyId?: number } | null>(null);
  const [partyFilter, setPartyFilter] = useState<number | null>(null);
  const record = can("payment.record") && writable;

  return (
    <div className="space-y-6">
      <PageHeader
        title={received ? "Receivables" : "Payables"}
        description={received ? "What customers owe you on posted invoices, by how overdue it is." : "What you owe suppliers on posted bills, by how overdue it is."}
        actions={record ? <button type="button" className="btn-primary" onClick={() => setRecording({})}>{received ? "Record money received" : "Record a payment"}</button> : null}
      />
      {data.isLoading ? <Loading /> : data.error || !data.data ? <ErrorText error={data.error} /> : (
        <>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <div className="card"><p className="text-xs text-gray-500">Outstanding</p><p className="text-lg font-bold">{rupees(data.data.total)}</p></div>
            {data.data.buckets.map((b) => (
              <div key={b.key} className="card">
                <p className="text-xs text-gray-500">{b.key === "current" ? b.label : `Overdue ${b.label}`}</p>
                <p className={`text-lg font-bold ${b.key !== "current" && Number(b.amount) > 0 ? "text-red-700" : ""}`}>{rupees(b.amount)}</p>
              </div>
            ))}
          </div>

          <Card title={received ? "By customer" : "By supplier"} className="overflow-x-auto">
            {data.data.parties.length === 0 ? <Empty title="Nothing outstanding">{received ? "Every posted invoice is paid." : "Every posted bill is paid."}</Empty> : (
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">{received ? "Customer" : "Supplier"}</th>
                    <th scope="col" className="table-head text-right">Outstanding</th>
                    <th scope="col" className="table-head text-right">Overdue</th>
                    <th scope="col" className="table-head text-right">90+ days</th>
                    <th scope="col" className="table-head text-right">Advance held</th>
                    <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.data.parties.map((p) => (
                    <tr key={p.party.id} className={partyFilter === p.party.id ? "bg-navy-50" : ""}>
                      <td className="table-cell">
                        <button type="button" className="font-semibold text-navy-900 hover:underline" onClick={() => setPartyFilter(partyFilter === p.party.id ? null : p.party.id)}>{p.party.name}</button>
                        <p className="text-xs text-gray-500">{p.documents} {received ? "invoice" : "bill"}{p.documents === 1 ? "" : "s"}</p>
                      </td>
                      <td className="table-cell text-right text-sm">{rupees(p.outstanding)}</td>
                      <td className={`table-cell text-right text-sm ${Number(p.overdue) > 0 ? "font-semibold text-red-700" : ""}`}>{rupees(p.overdue)}</td>
                      <td className="table-cell text-right text-sm">{rupees(p.d90_plus)}</td>
                      <td className="table-cell text-right text-sm">{Number(p.advance) > 0 ? rupees(p.advance) : "—"}</td>
                      <td className="table-cell text-right">
                        {record ? <button type="button" className="btn-link" onClick={() => setRecording({ partyId: p.party.id })}>{received ? "Record receipt" : "Pay"}</button> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          {data.data.documents.length ? (
            <Card title={partyFilter ? `Open ${received ? "invoices" : "bills"}: ${data.data.parties.find((p) => p.party.id === partyFilter)?.party.name}` : `Open ${received ? "invoices" : "bills"}`} className="overflow-x-auto">
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">{received ? "Invoice" : "Their invoice"}</th>
                    <th scope="col" className="table-head">{received ? "Customer" : "Supplier"}</th>
                    <th scope="col" className="table-head">Due</th>
                    <th scope="col" className="table-head text-right">Total</th>
                    <th scope="col" className="table-head text-right">Paid</th>
                    <th scope="col" className="table-head text-right">Outstanding</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.data.documents.filter((d) => !partyFilter || d.party.id === partyFilter).map((d) => (
                    <tr key={d.id}>
                      <td className="table-cell text-sm"><span className="font-semibold">{d.number}</span><p className="text-xs text-gray-500">{day(d.date)}{received ? "" : ` · our ${d.our_number}`}</p></td>
                      <td className="table-cell text-sm">{d.party.name}</td>
                      <td className="table-cell text-sm">{day(d.due_date)}<div className="mt-1 flex flex-wrap gap-1"><Badge tone={PAYMENT_TONES[d.status] ?? "gray"}>{d.status_label}</Badge>{d.msme ? <Badge tone="orange">MSME</Badge> : null}</div></td>
                      <td className="table-cell text-right text-sm">{rupees(d.total)}</td>
                      <td className="table-cell text-right text-sm">{rupees(d.paid)}{Number(d.pending) > 0 ? <p className="text-xs text-amber-700">{rupees(d.pending)} awaiting approval</p> : null}</td>
                      <td className="table-cell text-right text-sm font-semibold">{rupees(d.outstanding)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : null}
        </>
      )}
      {recording ? <PaymentModal direction={direction} partyId={recording.partyId} onClose={() => setRecording(null)} /> : null}
    </div>
  );
}
