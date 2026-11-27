import { api, openPdf, unwrap, type Schemas } from "@makesetu/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, Modal, PageHeader } from "../../components/ui";
import { day, quantity } from "../../lib/format";

type Batch = Schemas["BatchOut"];

/** Batches and serial numbers: what's on hand, what expires soon, a scan box, the trace, and labels. */
export function BatchesPage() {
  const [q, setQ] = useState("");
  const [expiring, setExpiring] = useState(false);
  const [all, setAll] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [scan, setScan] = useState("");
  const [scanned, setScanned] = useState<{ code: string; found: Batch[] } | null>(null);
  const [labelError, setLabelError] = useState("");

  const batches = useQuery({
    queryKey: ["batches", "list", q, expiring, all],
    queryFn: async () => unwrap(await api.GET("/api/v1/batches", {
      params: { query: { q, in_stock: !all, ...(expiring ? { expiring_within: 30 } : {}) } },
    })),
  });

  const lookup = async (code: string) => {
    if (!code.trim()) return;
    const found = unwrap(await api.GET("/api/v1/batches/lookup", { params: { query: { code: code.trim() } } }));
    if (found.length === 1) {
      setOpenId(found[0].id);
      setScanned(null);
    } else {
      setScanned({ code: code.trim(), found });
    }
    setScan("");
  };

  const labels = async (ids: number[]) => {
    setLabelError("");
    try {
      await openPdf(`/api/v1/batch-labels/pdf?ids=${ids.join(",")}`);
    } catch (err) {
      setLabelError(err instanceof Error ? err.message : "Couldn't open the labels.");
    }
  };
  const rows = batches.data ?? [];
  const toggle = (id: number) => setSelected(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Batches and serial numbers"
        description="Stock of items tracked by batch or serial number. Scan a label, or open a batch to see where it came from, what it went into and who it was sent to."
        actions={selected.length ? (
          <button type="button" className="btn-primary" onClick={() => labels(selected)}>Print {selected.length} label{selected.length === 1 ? "" : "s"}</button>
        ) : null}
      />
      {labelError ? <p className="text-sm text-red-600">{labelError}</p> : null}
      <Card>
        <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); lookup(scan); }}>
          <label className="flex-1 text-sm font-semibold text-gray-700">
            Scan a label
            <input className="field-input mt-1 font-mono" value={scan} onChange={(e) => setScan(e.target.value)} autoFocus
              placeholder="Scan the barcode, or type a batch, heat or serial number, then Enter" />
          </label>
          <button type="submit" className="btn-secondary">Find</button>
        </form>
        {scanned ? (
          scanned.found.length === 0 ? <p className="mt-2 text-sm text-red-700">Nothing matches “{scanned.code}”.</p> : (
            <div className="mt-2 text-sm">
              <p className="text-gray-600">“{scanned.code}” is on more than one item:</p>
              {scanned.found.map((b) => (
                <button key={b.id} type="button" className="btn-link mr-3" onClick={() => setOpenId(b.id)}>{b.item_code} {b.number}</button>
              ))}
            </div>
          )
        ) : null}
      </Card>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <input className="field-input w-64" placeholder="Search item, batch or heat no." value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search batches" />
        <label className="flex items-center gap-2"><input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={expiring} onChange={(e) => setExpiring(e.target.checked)} />Expired or expiring in 30 days</label>
        <label className="flex items-center gap-2"><input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={all} onChange={(e) => setAll(e.target.checked)} />Include used up</label>
      </div>
      <Card className="overflow-x-auto p-0">
        {batches.isLoading ? <Loading /> : batches.error ? <div className="p-5"><ErrorText error={batches.error} /></div> : rows.length === 0 ? (
          <div className="p-5"><Empty title="No batches here">Set an item to be tracked by batch or serial number (Items, Edit), then receive or make it.</Empty></div>
        ) : (
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head w-8">
                  <input type="checkbox" className="rounded border-gray-300 text-accent-500" aria-label="Select all for labels"
                    checked={selected.length === rows.length} onChange={(e) => setSelected(e.target.checked ? rows.map((b) => b.id) : [])} />
                </th>
                <th scope="col" className="table-head">Batch / serial no.</th>
                <th scope="col" className="table-head">Item</th>
                <th scope="col" className="table-head">Expires</th>
                <th scope="col" className="table-head text-right">On hand</th>
                <th scope="col" className="table-head">Came from</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((b) => (
                <tr key={b.id} className="hover:bg-gray-50">
                  <td className="table-cell"><input type="checkbox" className="rounded border-gray-300 text-accent-500" aria-label={`Select ${b.number}`} checked={selected.includes(b.id)} onChange={() => toggle(b.id)} /></td>
                  <td className="table-cell">
                    <button type="button" className="font-mono font-semibold text-navy-900 hover:underline" onClick={() => setOpenId(b.id)}>{b.number}</button>
                    {b.supplier_batch ? <p className="text-xs text-gray-500">Heat / their batch {b.supplier_batch}</p> : null}
                  </td>
                  <td className="table-cell text-sm">{b.item_name}<p className="text-xs text-gray-500">{b.item_code}{b.tracking === "serial" ? " · serial no." : ""}</p></td>
                  <td className="table-cell text-sm">{b.expires_on ? (b.expired ? <Badge tone="red">Expired {day(b.expires_on)}</Badge> : day(b.expires_on)) : "—"}</td>
                  <td className="table-cell text-right text-sm">
                    {quantity(b.on_hand, b.unit)}
                    {b.stock.map((s) => <p key={s.warehouse_id} className="text-xs text-gray-500">{quantity(s.quantity)} {s.warehouse_name}</p>)}
                  </td>
                  <td className="table-cell text-sm">{b.origin_label} {b.reference}<p className="text-xs text-gray-500">{b.supplier_name}</p></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {openId ? <BatchDetail batchId={openId} onOpen={setOpenId} onLabel={(id) => labels([id])} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function BatchDetail({ batchId, onOpen, onLabel, onClose }: {
  batchId: number; onOpen: (id: number) => void; onLabel: (id: number) => void; onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["batches", "detail", batchId],
    queryFn: async () => unwrap(await api.GET("/api/v1/batches/{batch_id}", { params: { path: { batch_id: batchId } } })),
  });
  const b = detail.data?.batch;
  const links = (title: string, list: Batch[]) => (
    <div>
      <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
      {list.length === 0 ? <p className="text-sm text-gray-500">—</p> : (
        <ul className="mt-1 space-y-0.5 text-sm">
          {list.map((x) => (
            <li key={x.id}><button type="button" className="btn-link font-mono" onClick={() => onOpen(x.id)}>{x.number}</button> <span className="text-gray-500">{x.item_name}</span></li>
          ))}
        </ul>
      )}
    </div>
  );
  return (
    <Modal wide title={b ? `${b.tracking === "serial" ? "Serial no." : "Batch"} ${b.number}` : "Batch"} onClose={onClose} footer={
      <>
        {b ? <button type="button" className="btn-secondary mr-auto" onClick={() => onLabel(b.id)}>Print label</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      {detail.isLoading ? <Loading /> : detail.error ? <ErrorText error={detail.error} /> : b && detail.data ? (
        <>
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="text-gray-500">Item</dt><dd>{b.item_name} <span className="text-gray-500">{b.item_code}</span></dd></div>
            <div><dt className="text-gray-500">On hand</dt><dd>{quantity(b.on_hand, b.unit)}</dd></div>
            <div><dt className="text-gray-500">Came from</dt><dd>{b.origin_label} {b.reference}{b.supplier_name ? ` · ${b.supplier_name}` : ""}</dd></div>
            {b.supplier_batch ? <div><dt className="text-gray-500">Heat / their batch</dt><dd className="font-mono">{b.supplier_batch}</dd></div> : null}
            {b.manufactured_on ? <div><dt className="text-gray-500">Made on</dt><dd>{day(b.manufactured_on)}</dd></div> : null}
            {b.expires_on ? <div><dt className="text-gray-500">Expires</dt><dd className={b.expired ? "font-semibold text-red-700" : ""}>{day(b.expires_on)}</dd></div> : null}
          </dl>
          <div className="grid gap-4 sm:grid-cols-2">
            {links("Made from", detail.data.made_from)}
            {links("Went into", detail.data.made_into)}
          </div>
          <table className="w-full">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Date</th>
                <th scope="col" className="table-head">Movement</th>
                <th scope="col" className="table-head">Where</th>
                <th scope="col" className="table-head text-right">Quantity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {detail.data.movements.map((m, i) => (
                <tr key={i}>
                  <td className="table-cell text-sm">{day(m.entry_date)}</td>
                  <td className="table-cell text-sm">{m.kind_label} <span className="text-gray-500">{m.reference}</span>{m.note ? <p className="text-xs text-gray-500">{m.note}</p> : null}</td>
                  <td className="table-cell text-sm">{m.warehouse_name}</td>
                  <td className={`table-cell text-right text-sm ${Number(m.quantity) < 0 ? "text-red-700" : "text-green-700"}`}>{Number(m.quantity) > 0 ? "+" : ""}{quantity(m.quantity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </Modal>
  );
}
