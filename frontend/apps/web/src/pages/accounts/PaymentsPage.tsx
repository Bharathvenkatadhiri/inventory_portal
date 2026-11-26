import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, ErrorText, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { ReasonModal, StatusBadge } from "../purchase/PurchaseLayout";

type Payment = Schemas["PartyPaymentOut"];

export function PaymentsPage() {
  const [direction, setDirection] = useState("");
  const [status, setStatus] = useState("");
  const payments = useQuery({
    queryKey: ["payments", direction, status],
    queryFn: async () => unwrap(await api.GET("/api/v1/payments", { params: { query: { direction, status } } })),
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const open = (payments.data ?? []).find((p) => p.id === openId);
  return (
    <div className="space-y-6">
      <PageHeader title="Payments" description="Money received from customers and paid to suppliers. Record them from Receivables or Payables." />
      <div className="flex flex-wrap gap-3">
        <select className="field-input max-w-[220px]" aria-label="Direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
          <option value="">Received and paid</option>
          <option value="received">Received</option>
          <option value="made">Paid</option>
        </select>
        <select className="field-input max-w-[220px]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          <option value="pending_approval">Waiting for approval</option>
          <option value="posted">Posted</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      <Card className="overflow-x-auto p-0">
        {payments.isLoading ? <Loading /> : payments.error ? <div className="p-5"><ErrorText error={payments.error} /></div> : (payments.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="No payments yet" /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Number</th>
                <th scope="col" className="table-head">Party</th>
                <th scope="col" className="table-head">How</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head text-right">Amount</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(payments.data ?? []).map((p) => (
                <tr key={p.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setOpenId(p.id)}>
                  <td className="table-cell"><button type="button" className="font-semibold text-navy-900 hover:underline">{p.number}</button><p className="text-xs text-gray-500">{day(p.payment_date)}</p></td>
                  <td className="table-cell text-sm">{p.party.name}<p className="text-xs text-gray-500">{p.direction === "received" ? "Received from" : "Paid to"}</p></td>
                  <td className="table-cell text-sm">{p.mode_label}{p.reference ? <p className="text-xs text-gray-500">{p.reference}</p> : null}</td>
                  <td className="table-cell"><StatusBadge status={p.status} label={p.status_label} /></td>
                  <td className={`table-cell text-right text-sm font-semibold ${p.direction === "received" ? "text-green-700" : ""}`}>{p.direction === "received" ? "+" : "−"}{rupees(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open ? <PaymentDetail payment={open} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function PaymentDetail({ payment: p, onClose }: { payment: Payment; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState<"reject" | "cancel" | null>(null);
  const refresh = () => { for (const key of ["payments", "outstanding", "sales-invoices", "purchase-bills"]) queryClient.invalidateQueries({ queryKey: [key] }); };
  const path = { params: { path: { payment_id: p.id } } };
  const approve = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/payments/{payment_id}/approve", path)), onSuccess: refresh });
  return (
    <Modal title={`${p.number} · ${p.party.name}`} onClose={onClose} footer={
      <>
        {p.actions.cancel ? <button type="button" className="btn-danger mr-auto" onClick={() => setAsking("cancel")}>Cancel payment</button> : null}
        {p.actions.approve ? <button type="button" className="btn-secondary" onClick={() => setAsking("reject")}>Reject</button> : null}
        {p.actions.approve ? <button type="button" className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}>Approve</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={p.status} label={p.status_label} />
        <span>{p.direction_label} on {day(p.payment_date)}</span>
      </div>
      {p.warnings.map((w) => <Notice key={w} tone="amber">{w}</Notice>)}
      {p.cancel_reason || p.rejection_reason ? <Notice tone="red">{p.rejection_reason ? `Rejected: ${p.rejection_reason}` : `Cancelled: ${p.cancel_reason}`}</Notice> : null}
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div><dt className="text-gray-500">Amount</dt><dd className="text-lg font-bold">{rupees(p.amount)}</dd></div>
        <div><dt className="text-gray-500">How</dt><dd>{p.mode_label}{p.reference ? ` · ${p.reference}` : ""}</dd>{p.bank_account ? <dd className="text-xs text-gray-500">{p.bank_account}</dd> : null}</div>
        <div><dt className="text-gray-500">Recorded by</dt><dd>{p.created_by || "—"}</dd></div>
        {p.approved_by ? <div><dt className="text-gray-500">Approved by</dt><dd>{p.approved_by}</dd></div> : null}
      </dl>
      <div>
        <p className="field-label">Settled against</p>
        {p.allocations.length === 0 ? <p className="text-sm text-gray-500">Nothing yet.</p> : (
          <ul className="divide-y divide-gray-100 text-sm">
            {p.allocations.map((a) => <li key={a.document_id} className="flex justify-between py-1"><span>{a.number}</span><span>{rupees(a.amount)}</span></li>)}
          </ul>
        )}
        {Number(p.unallocated) > 0 ? <p className="mt-1 text-sm text-gray-600">{rupees(p.unallocated)} held as an advance.</p> : null}
      </div>
      {p.notes ? <p className="text-sm text-gray-600">{p.notes}</p> : null}
      <ErrorText error={approve.error} />
      {asking ? (
        <ReasonModal
          title={asking === "reject" ? "Reject this payment" : "Cancel this payment"}
          label={asking === "reject" ? "Why is it rejected?" : "Why? For example, the cheque bounced or it was entered twice."}
          button={asking === "reject" ? "Reject" : "Cancel payment"}
          onClose={() => setAsking(null)}
          send={async (reason) => {
            if (asking === "reject") unwrap(await api.POST("/api/v1/payments/{payment_id}/reject", { ...path, body: { reason } }));
            else unwrap(await api.POST("/api/v1/payments/{payment_id}/cancel", { ...path, body: { reason } }));
            refresh();
            setAsking(null);
          }}
        />
      ) : null}
    </Modal>
  );
}
