import { api, downloadFile, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { useActive, useCan, useWritable } from "../../lib/session";
import { todayIso } from "../purchase/PurchaseLayout";

/** "2026-Q3" for today: quarters run April–June (Q1) to January–March (Q4). */
function currentQuarter(): string {
  const d = new Date();
  const fyStart = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  const q = Math.floor(((d.getMonth() - 3 + 12) % 12) / 3) + 1;
  return `${fyStart}-Q${q}`;
}

function quarterOptions(): { value: string; label: string }[] {
  const out = [];
  const [year, q] = currentQuarter().split("-Q").map(Number);
  for (let i = 0; i < 8; i++) {
    const qq = ((q - 1 - i) % 4 + 4) % 4 + 1;
    const fy = year - Math.floor((i + 4 - q) / 4);
    const months = ["Apr–Jun", "Jul–Sep", "Oct–Dec", "Jan–Mar"][qq - 1];
    out.push({ value: `${fy}-Q${qq}`, label: `Q${qq} FY ${fy}-${String((fy + 1) % 100).padStart(2, "0")} (${months})` });
  }
  return out;
}

/** TDS deducted on supplier bills: what's due, deposits, and the 26Q file. */
export function TdsPage() {
  const can = useCan();
  const writable = useWritable();
  const { company } = useActive();
  const [quarter, setQuarter] = useState(currentQuarter());
  const register = useQuery({
    queryKey: ["tds-register", quarter],
    queryFn: async () => unwrap(await api.GET("/api/v1/tds/register", { params: { query: { quarter } } })),
  });
  const [depositFor, setDepositFor] = useState<{ month: string; section: string; amount: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const r = register.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="TDS"
        description="TDS you deduct from suppliers. It's worked out on each bill when it's posted, using the section set on the supplier under Partners. Deposit each month's TDS by the 7th of the next month (30 April for March)."
        actions={
          <button type="button" className="btn-secondary" onClick={async () => {
            setError(null);
            try { await downloadFile(`/api/v1/tds/26q?quarter=${quarter}`); } catch (e) { setError(e); }
          }}>26Q rows (CSV)</button>
        }
      />
      <select className="field-input max-w-xs" aria-label="Quarter" value={quarter} onChange={(e) => setQuarter(e.target.value)}>
        {quarterOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {!company.tan ? <Notice tone="amber">Add your TAN under Settings → Company; the 26Q return needs it.</Notice> : null}
      <ErrorText error={error} />
      {register.isLoading ? <Loading /> : register.error || !r ? <ErrorText error={register.error} /> : (
        <>
          <Card title="By month" className="overflow-x-auto">
            {r.months.length === 0 ? <Empty title="No TDS this quarter" /> : (
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">Month · section</th>
                    <th scope="col" className="table-head text-right">Deducted</th>
                    <th scope="col" className="table-head text-right">Deposited</th>
                    <th scope="col" className="table-head text-right">To deposit</th>
                    <th scope="col" className="table-head">Due</th>
                    <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {r.months.map((m) => (
                    <tr key={`${m.month}-${m.section}`}>
                      <td className="table-cell text-sm">{new Date(m.month).toLocaleDateString("en-IN", { month: "long", year: "numeric" })}<p className="text-xs text-gray-500">{m.section_label}</p></td>
                      <td className="table-cell text-right text-sm">{rupees(m.deducted)}</td>
                      <td className="table-cell text-right text-sm">{rupees(m.deposited)}</td>
                      <td className={`table-cell text-right text-sm font-semibold ${m.overdue ? "text-red-700" : ""}`}>{rupees(m.balance)}</td>
                      <td className="table-cell text-sm">
                        {day(m.due_date)}
                        {m.overdue ? <p className="text-xs text-red-700">Overdue · interest if paid today {rupees(m.interest_if_paid_today)}</p> : null}
                      </td>
                      <td className="table-cell text-right">
                        {Number(m.balance) > 0 && can("payment.record") && writable ? (
                          <button type="button" className="btn-link" onClick={() => setDepositFor({ month: m.month, section: m.section, amount: String(Number(m.balance)) })}>Record deposit</button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Deductions" className="overflow-x-auto">
            {r.deductions.length === 0 ? <p className="text-sm text-gray-500">None this quarter.</p> : (
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">Bill</th>
                    <th scope="col" className="table-head">Deductee · PAN</th>
                    <th scope="col" className="table-head">Section</th>
                    <th scope="col" className="table-head text-right">On</th>
                    <th scope="col" className="table-head text-right">TDS</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {r.deductions.map((d) => (
                    <tr key={d.bill_id}>
                      <td className="table-cell text-sm">{d.supplier_invoice_number}<p className="text-xs text-gray-500">{day(d.date)} · {d.bill_number}</p></td>
                      <td className="table-cell text-sm">{d.supplier.name}<p className="font-mono text-xs text-gray-500">{d.pan || "No PAN (20%)"}</p></td>
                      <td className="table-cell text-sm">{d.section_label.split(" · ")[0]} @ {Number(d.rate)}%</td>
                      <td className="table-cell text-right text-sm">{rupees(d.base)}</td>
                      <td className="table-cell text-right text-sm font-semibold">{rupees(d.tds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Deposits (challans)">
            {r.deposits.length === 0 ? <p className="text-sm text-gray-500">None recorded this quarter.</p> : (
              <ul className="divide-y divide-gray-100 text-sm">
                {r.deposits.map((d) => (
                  <li key={d.id} className="flex flex-wrap justify-between gap-2 py-2">
                    <span>{d.section_label.split(" · ")[0]} for {new Date(d.period).toLocaleDateString("en-IN", { month: "short", year: "numeric" })} · BSR {d.bsr_code} · challan {d.challan_serial} · {day(d.deposited_on)}</span>
                    <span className="font-semibold">{rupees(d.amount)}{Number(d.interest) ? ` + ${rupees(d.interest)} interest` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
      {depositFor ? <DepositModal {...depositFor} onClose={() => setDepositFor(null)} /> : null}
    </div>
  );
}

function DepositModal({ month, section, amount: initial, onClose }: { month: string; section: string; amount: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState(initial);
  const [interest, setInterest] = useState("");
  const [paidOn, setPaidOn] = useState(todayIso());
  const [bsr, setBsr] = useState("");
  const [serial, setSerial] = useState("");
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/tds/deposits", {
      body: { period: month, section, amount, interest: interest || "0", deposited_on: paidOn, bsr_code: bsr, challan_serial: serial },
    })),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["tds-register"] }); onClose(); },
  });
  return (
    <Modal title="Record a TDS deposit" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Record</button>
      </>
    }>
      <p className="text-sm text-gray-600">From the challan (ITNS 281) counterfoil.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount (₹)">{(id) => <input id={id} className="field-input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}</Field>
        <Field label="Interest paid (₹)" hint="For a late deposit.">{(id) => <input id={id} className="field-input" inputMode="decimal" value={interest} onChange={(e) => setInterest(e.target.value)} />}</Field>
        <Field label="Deposited on">{(id) => <input id={id} type="date" className="field-input" value={paidOn} max={todayIso()} onChange={(e) => setPaidOn(e.target.value)} />}</Field>
        <Field label="BSR code" hint="7 digits.">{(id) => <input id={id} className="field-input font-mono" inputMode="numeric" maxLength={7} value={bsr} onChange={(e) => setBsr(e.target.value.replace(/\D/g, ""))} />}</Field>
        <Field label="Challan serial no.">{(id) => <input id={id} className="field-input font-mono" inputMode="numeric" maxLength={5} value={serial} onChange={(e) => setSerial(e.target.value.replace(/\D/g, ""))} />}</Field>
      </div>
      <ErrorText error={save.error} />
    </Modal>
  );
}
