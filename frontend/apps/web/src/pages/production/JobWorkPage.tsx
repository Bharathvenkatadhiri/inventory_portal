import { api, downloadFile, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PdfButton } from "../../components/PdfButton";
import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { day, quantity, rupees } from "../../lib/format";
import { useWritable } from "../../lib/session";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { ReasonModal, StatusBadge, todayIso } from "../purchase/PurchaseLayout";
import { batchText } from "../inventory/batches";

type Challan = Schemas["JobWorkChallanOut"];
const STATUS_TONE: Record<string, string> = { open: "issued", partly: "partially_received", closed: "received", cancelled: "cancelled" };

/** Goods sent to job workers, what's still with them, and what came back. */
export function JobWorkPage() {
  const writable = useWritable();
  const [status, setStatus] = useState("open");
  const challans = useQuery({
    queryKey: ["job-work", status],
    queryFn: async () => unwrap(await api.GET("/api/v1/job-work", { params: { query: { status } } })),
  });
  const [sending, setSending] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [itc, setItc] = useState(false);
  const open = (challans.data ?? []).find((c) => c.id === openId);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Job work"
        description="Goods sent to job workers for plating, heat treatment, machining and so on. They stay your stock while away, under At job workers. Inputs must come back within a year (s.143), or GST becomes payable on them."
        actions={
          <div className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={() => setItc(true)}>ITC-04</button>
            {writable ? <button type="button" className="btn-primary" onClick={() => setSending(true)}>Send for job work</button> : null}
          </div>
        }
      />
      <select className="field-input max-w-[220px]" aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="open">With job workers</option>
        <option value="closed">All back</option>
        <option value="">All</option>
      </select>
      <Card className="overflow-x-auto p-0">
        {challans.isLoading ? <Loading /> : challans.error ? <div className="p-5"><ErrorText error={challans.error} /></div> : (challans.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title={status === "open" ? "Nothing with job workers" : "No challans"} /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Challan</th>
                <th scope="col" className="table-head">Job worker · job</th>
                <th scope="col" className="table-head">Still with them</th>
                <th scope="col" className="table-head">Back by (s.143)</th>
                <th scope="col" className="table-head">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(challans.data ?? []).map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setOpenId(c.id)}>
                  <td className="table-cell"><button type="button" className="font-semibold text-navy-900 hover:underline">{c.number}</button><p className="text-xs text-gray-500">{day(c.challan_date)} · {rupees(c.value)}</p></td>
                  <td className="table-cell text-sm">{c.job_worker.name}<p className="text-xs text-gray-500">{c.process}</p></td>
                  <td className="table-cell text-sm">{c.lines.filter((l) => Number(l.pending_quantity) > 0).map((l) => <p key={l.id}>{quantity(l.pending_quantity, l.unit)} {l.item_name}</p>)}</td>
                  <td className="table-cell text-sm">
                    {day(c.due_back)}
                    {c.age_flag === "overdue" ? <div className="mt-1"><Badge tone="red">Over a year: GST payable</Badge></div> : null}
                    {c.age_flag === "due_soon" ? <div className="mt-1"><Badge tone="amber">Due back soon</Badge></div> : null}
                  </td>
                  <td className="table-cell"><StatusBadge status={STATUS_TONE[c.status] ?? c.status} label={c.status_label} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {sending ? <SendForm onClose={() => setSending(false)} /> : null}
      {open ? <ChallanDetail challan={open} onClose={() => setOpenId(null)} /> : null}
      {itc ? <Itc04Modal onClose={() => setItc(false)} /> : null}
    </div>
  );
}

function refreshAll(queryClient: ReturnType<typeof useQueryClient>) {
  for (const key of ["job-work", "stock", "items", "qc-lots"]) queryClient.invalidateQueries({ queryKey: [key] });
}

type Line = { key: number; item_id: number | null; quantity: string };
let nextKey = 1;

function SendForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const workers = useQuery({ queryKey: ["purchase-suppliers"], queryFn: async () => unwrap(await api.GET("/api/v1/purchase/suppliers")) });
  const items = useItems();
  const warehouses = useWarehouses();
  const stores = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const stockItems = (items.data ?? []).filter((i) => i.is_active && i.tracks_stock);
  const [workerId, setWorkerId] = useState<number | null>(null);
  const [process, setProcess] = useState("");
  const [fromId, setFromId] = useState<number | null>(null);
  const [sentOn, setSentOn] = useState(todayIso());
  const [returnBy, setReturnBy] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [lines, setLines] = useState<Line[]>([{ key: nextKey++, item_id: null, quantity: "" }]);
  const source = fromId ?? stores.find((w) => w.is_default)?.id ?? stores[0]?.id ?? null;
  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/job-work", {
      body: { job_worker_id: workerId!, process, from_warehouse_id: source!, challan_date: sentOn, expected_return: returnBy || null,
              vehicle_number: vehicle, notes: "", lines: lines.filter((l) => l.item_id).map((l) => ({ item_id: l.item_id!, quantity: l.quantity, batches: [] })) },
    })),
    onSuccess: () => { refreshAll(queryClient); onClose(); },
  });
  return (
    <Modal wide title="Send for job work" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!workerId || !process.trim() || save.isPending} onClick={() => save.mutate()}>Send</button>
      </>
    }>
      {workers.isLoading || items.isLoading ? <Loading /> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Job worker" hint="One of your active suppliers.">
              {(id) => (
                <select id={id} className="field-input" value={workerId ?? ""} onChange={(e) => setWorkerId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Choose</option>
                  {(workers.data ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Job to be done">{(id) => <input id={id} className="field-input" maxLength={120} placeholder="e.g. Zinc plating" value={process} onChange={(e) => setProcess(e.target.value)} />}</Field>
            <Field label="From store">
              {(id) => (
                <select id={id} className="field-input" value={source ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
                  {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              )}
            </Field>
            <Field label="Vehicle">{(id) => <input id={id} className="field-input uppercase" maxLength={20} value={vehicle} onChange={(e) => setVehicle(e.target.value)} />}</Field>
            <Field label="Sent on">{(id) => <input id={id} type="date" className="field-input" value={sentOn} max={todayIso()} onChange={(e) => setSentOn(e.target.value)} />}</Field>
            <Field label="Expected back">{(id) => <input id={id} type="date" className="field-input" value={returnBy} onChange={(e) => setReturnBy(e.target.value)} />}</Field>
          </div>
          <table className="w-full">
            <thead className="border-b border-gray-100"><tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head w-40">Quantity</th><th scope="col" className="table-head w-10"><span className="sr-only">Remove</span></th></tr></thead>
            <tbody className="divide-y divide-gray-100">
              {lines.map((l, index) => {
                const item = stockItems.find((i) => i.id === l.item_id);
                return (
                  <tr key={l.key}>
                    <td className="table-cell">
                      <select className="field-input" aria-label={`Item ${index + 1}`} value={l.item_id ?? ""} onChange={(e) => setLine(l.key, { item_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Choose an item</option>
                        {stockItems.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                      </select>
                    </td>
                    <td className="table-cell"><div className="flex items-center gap-1"><input className="field-input" inputMode="decimal" aria-label={`Quantity ${index + 1}`} value={l.quantity} onChange={(e) => setLine(l.key, { quantity: e.target.value })} /><span className="text-xs text-gray-500">{item?.unit}</span></div></td>
                    <td className="table-cell">{lines.length > 1 ? <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove item ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button type="button" className="btn-link" onClick={() => setLines([...lines, { key: nextKey++, item_id: null, quantity: "" }])}>+ Add an item</button>
        </>
      )}
      <ErrorText error={save.error} />
    </Modal>
  );
}

function ChallanDetail({ challan: c, onClose }: { challan: Challan; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [receiving, setReceiving] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  return (
    <Modal wide title={`${c.number} · ${c.job_worker.name}`} onClose={onClose} footer={
      <>
        {c.can_change && c.receipts.length === 0 ? <button type="button" className="btn-danger mr-auto" onClick={() => setCancelling(true)}>Cancel challan</button> : null}
        <PdfButton url={`/api/v1/job-work/${c.id}/pdf`} label="Challan PDF" />
        {c.can_change ? <button type="button" className="btn-primary" onClick={() => setReceiving(true)}>Receive back</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
      </>
    }>
      <p className="text-sm">{c.process} · sent {day(c.challan_date)} from {c.from_warehouse}{c.vehicle_number ? ` · ${c.vehicle_number}` : ""} · back by {day(c.due_back)}</p>
      {c.age_flag === "overdue" ? <Notice tone="red">These inputs have been away over a year: under s.143 they're treated as supplied on the challan date, and GST is payable on them.</Notice> : null}
      {c.status === "cancelled" ? <Notice tone="red">Cancelled: {c.cancel_reason}</Notice> : null}
      <table className="w-full">
        <thead className="border-b border-gray-100"><tr><th scope="col" className="table-head">Item</th><th scope="col" className="table-head text-right">Sent</th><th scope="col" className="table-head text-right">Back</th><th scope="col" className="table-head text-right">Lost</th><th scope="col" className="table-head text-right">Still away</th></tr></thead>
        <tbody className="divide-y divide-gray-100">
          {c.lines.map((l) => (
            <tr key={l.id}>
              <td className="table-cell text-sm">
                {l.item_name} <span className="text-gray-500">{l.item_code}</span>
                {l.batches.length ? <p className="font-mono text-xs text-gray-500">{batchText(l.batches)}</p> : null}
              </td>
              <td className="table-cell text-right text-sm">{quantity(l.quantity, l.unit)}</td>
              <td className="table-cell text-right text-sm">{quantity(l.returned_quantity)}</td>
              <td className="table-cell text-right text-sm">{quantity(l.waste_quantity)}</td>
              <td className="table-cell text-right text-sm font-semibold">{quantity(l.pending_quantity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.receipts.length ? (
        <div>
          <p className="field-label">Received back</p>
          <ul className="divide-y divide-gray-100 text-sm">
            {c.receipts.map((r) => (
              <li key={r.id} className="py-1">
                <strong>{r.number}</strong> · {day(r.received_date)}{r.job_worker_challan ? ` · their challan ${r.job_worker_challan}` : ""} · into {r.into_warehouse}
                {r.lines.map((rl, i) => (
                  <p key={i} className="text-xs text-gray-600">{quantity(rl.output_quantity, rl.unit)} {rl.output_item_name} at {rupees(rl.rate)}{Number(rl.waste_quantity) ? ` · ${quantity(rl.waste_quantity)} lost` : ""}</p>
                ))}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {receiving ? <ReceiveForm challan={c} onClose={() => setReceiving(false)} /> : null}
      {cancelling ? (
        <ReasonModal title="Cancel this challan" label="Why? The goods go back into the store." button="Cancel challan" onClose={() => setCancelling(false)}
          send={async (reason) => {
            unwrap(await api.POST("/api/v1/job-work/{challan_id}/cancel", { params: { path: { challan_id: c.id } }, body: { reason } }));
            refreshAll(queryClient);
            setCancelling(false);
          }} />
      ) : null}
    </Modal>
  );
}

type Row = { input: string; waste: string; outputItem: number | null; output: string; charge: string };

function ReceiveForm({ challan, onClose }: { challan: Challan; onClose: () => void }) {
  const queryClient = useQueryClient();
  const items = useItems();
  const warehouses = useWarehouses();
  const stores = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const away = challan.lines.filter((l) => Number(l.pending_quantity) > 0);
  const [rows, setRows] = useState<Record<number, Row>>(Object.fromEntries(away.map((l) => [l.id, { input: String(Number(l.pending_quantity)), waste: "", outputItem: l.item_id, output: "", charge: "" }])));
  const [intoId, setIntoId] = useState<number | null>(null);
  const [theirChallan, setTheirChallan] = useState("");
  const [on, setOn] = useState(todayIso());
  const set = (id: number, patch: Partial<Row>) => setRows({ ...rows, [id]: { ...rows[id], ...patch } });
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/job-work/{challan_id}/receipts", {
      params: { path: { challan_id: challan.id } },
      body: {
        into_warehouse_id: intoId ?? stores.find((w) => w.is_default)?.id ?? null, received_date: on, job_worker_challan: theirChallan, notes: "",
        lines: away.map((l) => ({
          challan_line_id: l.id, input_quantity: rows[l.id].input || "0", waste_quantity: rows[l.id].waste || "0",
          output_item_id: rows[l.id].outputItem, output_quantity: rows[l.id].output === "" ? null : rows[l.id].output, charge_per_unit: rows[l.id].charge || "0", output_batches: [],
        })),
      },
    })),
    onSuccess: () => { refreshAll(queryClient); onClose(); },
  });
  return (
    <Modal wide title={`Receive back against ${challan.number}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Back</button>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Record</button>
      </>
    }>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Into store">
          {(id) => (
            <select id={id} className="field-input" value={intoId ?? stores.find((w) => w.is_default)?.id ?? ""} onChange={(e) => setIntoId(Number(e.target.value))}>
              {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Their challan no.">{(id) => <input id={id} className="field-input" maxLength={40} value={theirChallan} onChange={(e) => setTheirChallan(e.target.value)} />}</Field>
        <Field label="Received on">{(id) => <input id={id} type="date" className="field-input" value={on} max={todayIso()} onChange={(e) => setOn(e.target.value)} />}</Field>
      </div>
      {away.map((l) => (
        <div key={l.id} className="rounded-lg border border-gray-200 p-3">
          <p className="text-sm font-semibold">{l.item_name} <span className="font-normal text-gray-500">· {quantity(l.pending_quantity, l.unit)} still away</span></p>
          <div className="mt-2 grid gap-3 sm:grid-cols-5">
            <Field label="Processed">{(id) => <input id={id} className="field-input" inputMode="decimal" value={rows[l.id].input} onChange={(e) => set(l.id, { input: e.target.value })} />}</Field>
            <Field label="Lost / waste">{(id) => <input id={id} className="field-input" inputMode="decimal" value={rows[l.id].waste} onChange={(e) => set(l.id, { waste: e.target.value })} />}</Field>
            <div className="sm:col-span-2">
              <Field label="Came back as">
                {(id) => (
                  <select id={id} className="field-input" value={rows[l.id].outputItem ?? ""} onChange={(e) => set(l.id, { outputItem: Number(e.target.value) })}>
                    {(items.data ?? []).filter((i) => i.is_active && i.tracks_stock).map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                  </select>
                )}
              </Field>
            </div>
            <Field label="Quantity back" hint="Blank: same as processed.">{(id) => <input id={id} className="field-input" inputMode="decimal" value={rows[l.id].output} onChange={(e) => set(l.id, { output: e.target.value })} />}</Field>
          </div>
          <div className="mt-2 max-w-[220px]">
            <Field label="Job work charge (₹ a unit)" hint="Added to the cost; the bill comes separately.">{(id) => <input id={id} className="field-input" inputMode="decimal" value={rows[l.id].charge} onChange={(e) => set(l.id, { charge: e.target.value })} />}</Field>
          </div>
        </div>
      ))}
      <ErrorText error={save.error} />
    </Modal>
  );
}

function Itc04Modal({ onClose }: { onClose: () => void }) {
  const d = new Date();
  const fyStart = `${d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1}-04-01`;
  const [start, setStart] = useState(fyStart);
  const [end, setEnd] = useState(todayIso());
  const [error, setError] = useState<unknown>(null);
  const get = async (table: "sent" | "received") => {
    setError(null);
    try { await downloadFile(`/api/v1/job-work/itc04?start=${start}&end=${end}&table=${table}`); } catch (e) { setError(e); }
  };
  return (
    <Modal title="ITC-04" onClose={onClose} footer={<button type="button" className="btn-secondary" onClick={onClose}>Close</button>}>
      <p className="text-sm text-gray-600">Goods sent to and received from job workers, for the ITC-04 offline tool. File it half-yearly (turnover over ₹5 crore) or yearly.</p>
      <div className="grid grid-cols-2 gap-4">
        <Field label="From">{(id) => <input id={id} type="date" className="field-input" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="To">{(id) => <input id={id} type="date" className="field-input" value={end} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary" onClick={() => get("sent")}>Table 4: goods sent (CSV)</button>
        <button type="button" className="btn-primary" onClick={() => get("received")}>Table 5: received back (CSV)</button>
      </div>
      <ErrorText error={error} />
    </Modal>
  );
}
