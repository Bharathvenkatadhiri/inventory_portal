import { api, downloadFile, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, Notice, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";

const EFFECT: Record<string, { label: string; tone: "green" | "red" | "amber" | "gray" }> = {
  allowed: { label: "Deductible", tone: "green" },
  disallowed: { label: "Disallowed (43B(h))", tone: "red" },
  overdue: { label: "Overdue: pay by 31 March", tone: "amber" },
  open: { label: "Not due yet", tone: "gray" },
};

/** Accounts > MSME: paying micro and small suppliers within 45 days (s.43B(h), MSME Form 1). */
export function MsmePage() {
  const [fy, setFy] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const report = useQuery({
    queryKey: ["msme", fy],
    queryFn: async () => unwrap(await api.GET("/api/v1/accounts/msme", { params: { query: { fy } } })),
  });
  const data = report.data;
  const form1 = async (half: "H1" | "H2") => {
    setDownloadError("");
    try {
      await downloadFile(`/api/v1/accounts/msme/form1.csv?fy=${data!.fy}&half=${half}`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Couldn't download it.");
    }
  };
  return (
    <div className="space-y-6">
      <PageHeader
        title="MSME payments"
        description="Micro and small suppliers (manufacturing or services, with a Udyam registration) must be paid within the agreed credit period, at most 45 days, or 15 days if none was agreed. What's still unpaid after that at the year end can't be claimed as an expense that year (Income Tax Act s.43B(h)), and late payment carries compound interest at three times the RBI bank rate (MSMED Act s.16). Mark suppliers as MSMEs under Partners, Terms."
        actions={data ? (
          <select className="field-input" aria-label="Financial year" value={data.fy} onChange={(e) => setFy(e.target.value)}>
            {data.fy_options.map((o) => <option key={o} value={o}>FY {o}</option>)}
          </select>
        ) : null}
      />
      {report.isLoading ? <Loading /> : report.error || !data ? <ErrorText error={report.error} /> : (
        <>
          {data.due_soon.length ? (
            <Card title="Pay these now" description="Bills from micro and small suppliers due within a week, or already overdue.">
              <ul className="divide-y divide-gray-100">
                {data.due_soon.map((d) => (
                  <li key={d.id} className="flex items-center justify-between py-2 text-sm">
                    <span><span className="font-semibold">{d.supplier.name}</span> · bill {d.number}</span>
                    <span className="flex items-center gap-3">
                      {rupees(d.outstanding)}
                      <Badge tone={d.days_left < 0 ? "red" : "amber"}>{d.days_left < 0 ? `${-d.days_left} days overdue` : d.days_left === 0 ? "Due today" : `Due in ${d.days_left} days`}</Badge>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {([
              ["Paid on time", data.summary.paid_on_time, ""],
              ["Paid late", data.summary.paid_late, "text-amber-700"],
              ["Not due yet", data.summary.open, ""],
              ["Overdue, unpaid", data.summary.overdue, "text-red-700"],
              ["Expense disallowed", data.summary.disallowed_expense, "text-red-700"],
              [`Interest (s.16, ${Number(data.bank_rate) * 3}%)`, data.summary.interest, "text-red-700"],
            ] as const).map(([label, value, tone]) => (
              <div key={label} className="card"><p className="text-xs text-gray-500">{label}</p><p className={`text-lg font-bold ${Number(value) ? tone : ""}`}>{rupees(value)}</p></div>
            ))}
          </div>
          {Number(data.summary.overdue) > 0 ? (
            <Notice tone="amber">Pay overdue MSME bills before 31 March and the expense is still allowed this year (interest is owed either way, and isn't deductible).</Notice>
          ) : null}
          <Card title={`Bills, FY ${data.fy}`} className="overflow-x-auto"
            actions={<div className="flex gap-2"><button type="button" className="btn-secondary" onClick={() => form1("H1")}>MSME-1 Apr–Sep</button><button type="button" className="btn-secondary" onClick={() => form1("H2")}>MSME-1 Oct–Mar</button></div>}>
            {downloadError ? <p className="text-sm text-red-600">{downloadError}</p> : null}
            {data.bills.length === 0 ? <Empty title="No bills from micro or small suppliers this year" /> : (
              <table className="w-full min-w-[860px]">
                <thead className="border-b border-gray-100">
                  <tr>
                    <th scope="col" className="table-head">Supplier · bill</th>
                    <th scope="col" className="table-head">Due by</th>
                    <th scope="col" className="table-head text-right">Payable</th>
                    <th scope="col" className="table-head text-right">On time</th>
                    <th scope="col" className="table-head text-right">Late</th>
                    <th scope="col" className="table-head text-right">Unpaid</th>
                    <th scope="col" className="table-head text-right">Interest</th>
                    <th scope="col" className="table-head">Tax effect</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.bills.map((b) => (
                    <tr key={b.id}>
                      <td className="table-cell text-sm"><span className="font-semibold">{b.supplier.name}</span> <Badge>{b.category}</Badge>
                        <p className="text-xs text-gray-500">{b.supplier_invoice_number} of {day(b.invoice_date)} · {b.number}</p></td>
                      <td className="table-cell text-sm">{day(b.msme_due_date)}{b.days_overdue ? <p className="text-xs text-red-700">{b.days_overdue} days over</p> : null}</td>
                      <td className="table-cell text-right text-sm">{rupees(b.payable)}</td>
                      <td className="table-cell text-right text-sm">{Number(b.paid_on_time) ? rupees(b.paid_on_time) : "—"}</td>
                      <td className="table-cell text-right text-sm">{Number(b.paid_late) ? rupees(b.paid_late) : "—"}</td>
                      <td className="table-cell text-right text-sm">{Number(b.unpaid) ? rupees(b.unpaid) : "—"}</td>
                      <td className="table-cell text-right text-sm">{Number(b.interest) ? rupees(b.interest) : "—"}</td>
                      <td className="table-cell text-sm"><Badge tone={EFFECT[b.effect]?.tone ?? "gray"}>{EFFECT[b.effect]?.label ?? b.effect}</Badge>
                        {Number(b.disallowed_expense) ? <p className="text-xs text-red-700">{rupees(b.disallowed_expense)} of expense</p> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="mt-3 text-xs text-gray-500">
              Days run from the supplier's invoice date. The disallowed expense is the unpaid share of the bill's value before GST (GST you took as input credit isn't an expense). Interest uses the RBI bank rate of {Number(data.bank_rate)}%; check it with your auditor.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
