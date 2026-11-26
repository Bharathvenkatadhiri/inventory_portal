import { api, downloadFile, unwrap } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, ErrorText, Loading, Notice, PageHeader } from "../../components/ui";
import { rupees } from "../../lib/format";

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

const FILES = [
  { kind: "gstr1", label: "GSTR-1 (JSON)", hint: "Upload in the GST offline tool or portal." },
  { kind: "gstr3b", label: "GSTR-3B (JSON)", hint: "Check ITC against GSTR-2B first." },
  { kind: "tally", label: "Tally (XML)", hint: "Gateway of Tally → Import → Vouchers." },
] as const;

const TONE = { error: "red", warning: "amber", info: "navy" } as const;

/** One month's GST returns, built from posted invoices, notes and bills. */
export function GstReturnsPage() {
  const [period, setPeriod] = useState(lastMonth());
  const summary = useQuery({
    queryKey: ["gst-returns", period],
    queryFn: async () => unwrap(await api.GET("/api/v1/gst-returns/summary", { params: { query: { period } } })),
  });
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const download = async (kind: string) => {
    setDownloading(kind);
    setError(null);
    try {
      await downloadFile(`/api/v1/gst-returns/${kind}?period=${period}`);
    } catch (err) {
      setError(err);
    } finally {
      setDownloading(null);
    }
  };
  const s = summary.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="GST returns"
        description="GSTR-1 and GSTR-3B for a month, worked out from your posted invoices, credit/debit notes and bills dated in it, plus a Tally import file. Review the figures and the checks, then download."
      />
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="field-label">Tax period</span>
          <input type="month" className="field-input" value={period} onChange={(e) => setPeriod(e.target.value)} />
        </label>
        {s ? <p className="pb-2 text-sm text-gray-500">GSTIN {s.gstin} · return period {s.fp}</p> : null}
      </div>
      {summary.isLoading ? <Loading /> : summary.error || !s ? <ErrorText error={summary.error} /> : (
        <>
          {s.checks.length ? (
            <div className="space-y-2">
              {s.checks.map((c, i) => <Notice key={i} tone={TONE[c.level as keyof typeof TONE] ?? "navy"}>{c.message}</Notice>)}
            </div>
          ) : <Notice tone="green">No problems found in this month's documents.</Notice>}

          <Card title="GSTR-1 · outward supplies" className="overflow-x-auto">
            <table className="w-full min-w-[560px]">
              <thead className="border-b border-gray-100">
                <tr>
                  <th scope="col" className="table-head">Table</th>
                  <th scope="col" className="table-head text-right">Entries</th>
                  <th scope="col" className="table-head text-right">Taxable value</th>
                  <th scope="col" className="table-head text-right">Tax</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {s.gstr1.map((row) => (
                  <tr key={row.key} className={row.count ? "" : "text-gray-400"}>
                    <td className="table-cell text-sm">{row.label}</td>
                    <td className="table-cell text-right text-sm">{row.count}</td>
                    <td className="table-cell text-right text-sm">{row.key === "doc_issue" ? "—" : rupees(row.taxable)}</td>
                    <td className="table-cell text-right text-sm">{row.key === "doc_issue" ? "—" : rupees(row.tax)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <Card title="GSTR-3B · summary" className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead className="border-b border-gray-100">
                <tr>
                  <th scope="col" className="table-head">Table</th>
                  <th scope="col" className="table-head text-right">Taxable value</th>
                  <th scope="col" className="table-head text-right">IGST</th>
                  <th scope="col" className="table-head text-right">CGST</th>
                  <th scope="col" className="table-head text-right">SGST</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {s.gstr3b.map((row) => (
                  <tr key={row.key}>
                    <td className="table-cell text-sm">{row.label}</td>
                    <td className="table-cell text-right text-sm">{row.key === "4a5" ? "—" : rupees(row.taxable)}</td>
                    <td className="table-cell text-right text-sm">{rupees(row.igst)}</td>
                    <td className="table-cell text-right text-sm">{rupees(row.cgst)}</td>
                    <td className="table-cell text-right text-sm">{rupees(row.sgst)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <Card title="Download">
            <div className="grid gap-4 sm:grid-cols-3">
              {FILES.map((f) => (
                <div key={f.kind}>
                  <button type="button" className="btn-primary w-full" disabled={downloading !== null} onClick={() => download(f.kind)}>
                    {downloading === f.kind ? "Preparing…" : f.label}
                  </button>
                  <p className="mt-1 text-xs text-gray-500">{f.hint}</p>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-gray-500">
              The files use the GST portal's field names. The portal changes its formats from time to time, so test one upload in the offline tool before relying on them.
              For Tally, create these ledgers first (or rename them in your company): Sales @18% (one per rate), Purchase @18%, Output/Input CGST, SGST and IGST, Round Off, Bank, Cash. Customer and supplier ledgers are created by the import.
            </p>
            <ErrorText error={error} />
          </Card>
        </>
      )}
    </div>
  );
}
