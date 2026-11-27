import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { day, quantity, rupees } from "../../lib/format";
import { useCan, useWritable } from "../../lib/session";
import { useItems, useWarehouses } from "../inventory/InventoryLayout";
import { QuoteFields, quoteBody, type QuoteTerms } from "../sales/RfqsReceivedPage";
import { ReasonModal, StatusBadge, todayIso } from "./PurchaseLayout";

type Rfq = Schemas["RfqOut"];
type Invite = Schemas["RfqInviteOut"];
const rfqKey = (id: number | string) => ["rfq", String(id)] as const;

function useRfq(id: number | string, enabled = true) {
  return useQuery({
    queryKey: rfqKey(id),
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/v1/rfqs/{rfq_id}", { params: { path: { rfq_id: Number(id) } } })),
  });
}

const TONE: Record<string, string> = { draft: "draft", sent: "awaiting", awarded: "accepted", cancelled: "cancelled" };

/** Purchase > RFQs: asking suppliers to quote, comparing, awarding. */
export function RfqsPage() {
  const can = useCan();
  const writable = useWritable();
  const list = useQuery({ queryKey: ["rfqs"], queryFn: async () => unwrap(await api.GET("/api/v1/rfqs")) });
  return (
    <div className="space-y-6">
      <PageHeader
        title="Requests for quotation"
        description="Ask several suppliers to quote for what you need. Suppliers on MakeSetu quote here themselves; enter others' quotes from their email. Compare line by line and award: purchase orders are drafted for you."
        actions={can("po.create") && writable ? <Link to="/purchase/rfqs/new" className="btn-primary">New RFQ</Link> : null}
      />
      <Card className="overflow-x-auto p-0">
        {list.isLoading ? <Loading /> : list.error ? <div className="p-5"><ErrorText error={list.error} /></div> : (list.data ?? []).length === 0 ? (
          <div className="p-5"><Empty title="No RFQs yet" /></div>
        ) : (
          <table className="w-full min-w-[640px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">RFQ</th>
                <th scope="col" className="table-head">Quote by</th>
                <th scope="col" className="table-head text-right">Quotes</th>
                <th scope="col" className="table-head">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(list.data ?? []).map((r) => (
                <tr key={r.id} className="hover:bg-gray-50">
                  <td className="table-cell"><Link to="/purchase/rfqs/$rfqId" params={{ rfqId: String(r.id) }} className="font-semibold text-navy-900 hover:underline">{r.title}</Link>
                    <p className="text-xs text-gray-500">{r.number} · {day(r.rfq_date)}</p></td>
                  <td className="table-cell text-sm">{r.respond_by ? day(r.respond_by) : "—"}</td>
                  <td className="table-cell text-right text-sm">{r.quoted} of {r.asked}</td>
                  <td className="table-cell"><StatusBadge status={TONE[r.status] ?? r.status} label={r.status_label} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export function RfqDetailPage() {
  const { rfqId } = useParams({ strict: false }) as { rfqId: string };
  const rfq = useRfq(rfqId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [entering, setEntering] = useState<Invite | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [adding, setAdding] = useState(false);
  const [awards, setAwards] = useState<Record<number, number | null>>({});
  const path = { params: { path: { rfq_id: Number(rfqId) } } };
  const refresh = (r?: Rfq) => {
    queryClient.invalidateQueries({ queryKey: ["rfqs"] });
    if (r) queryClient.setQueryData(rfqKey(r.id), r);
  };
  const award = useMutation({
    mutationFn: async (rows: Record<number, number | null>) => {
      const chosen = Object.entries(rows).filter(([, invite]) => invite !== null).map(([line, invite]) => ({ rfq_line_id: Number(line), invite_id: invite! }));
      const result = unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/award", { ...path, body: { invite_id: null, awards: chosen } }));
      refresh(result.rfq);
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
  });
  const act = useMutation({
    mutationFn: async (action: "send" | "delete") => {
      if (action === "send") return refresh(unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/send", path)));
      unwrap(await api.DELETE("/api/v1/rfqs/{rfq_id}", path));
      refresh();
      return navigate({ to: "/purchase/rfqs" });
    },
  });
  if (rfq.isLoading) return <Loading />;
  if (rfq.error || !rfq.data) return <ErrorText error={rfq.error} />;
  const r = rfq.data;
  const name = (inviteId: number) => r.suppliers.find((s) => s.id === inviteId)?.supplier.name ?? "";
  // Each line goes to its lowest quote unless chosen otherwise.
  const pick = (lineId: number) => {
    if (lineId in awards) return awards[lineId];
    const offers = r.lines.find((l) => l.line.id === lineId)?.offers ?? [];
    const best = [...offers].sort((a, b) => Number(a.rate) - Number(b.rate))[0];
    return best?.invite_id ?? null;
  };
  const awardRows = Object.fromEntries(r.lines.map((l) => [l.line.id, pick(l.line.id)]));
  const awardTotal = r.lines.reduce((sum, l) => sum + Number(l.offers.find((o) => o.invite_id === awardRows[l.line.id])?.value ?? 0), 0);
  return (
    <div className="space-y-6">
      <PageHeader
        title={r.title}
        description={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={TONE[r.status] ?? r.status} label={r.status_label} />
          {r.number} · {day(r.rfq_date)}{r.respond_by ? ` · quotes by ${day(r.respond_by)}` : ""}{r.required_by ? ` · needed by ${day(r.required_by)}` : ""} · to {r.ship_to}</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <PdfButton url={`/api/v1/rfqs/${r.id}/pdf`} label="PDF for suppliers" />
            {r.actions.edit ? <Link to="/purchase/rfqs/$rfqId/edit" params={{ rfqId: String(r.id) }} className="btn-secondary">Edit</Link> : null}
            {r.actions.edit ? <button type="button" className="btn-secondary" onClick={() => act.mutate("delete")}>Delete</button> : null}
            {r.actions.send ? <button type="button" className="btn-primary" onClick={() => act.mutate("send")}>Send to suppliers</button> : null}
            {r.actions.enter_quotes ? <button type="button" className="btn-secondary" onClick={() => setAdding(true)}>Ask another supplier</button> : null}
            {r.actions.cancel ? <button type="button" className="btn-danger" onClick={() => setCancelling(true)}>Cancel RFQ</button> : null}
          </div>
        }
      />
      <ErrorText error={act.error ?? award.error} />
      {r.cancel_reason ? <Notice tone="red">Cancelled: {r.cancel_reason}</Notice> : null}

      <Card title="Suppliers asked" className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Supplier</th>
              <th scope="col" className="table-head">Status</th>
              <th scope="col" className="table-head text-right">Quoted</th>
              <th scope="col" className="table-head text-right">Total with GST</th>
              <th scope="col" className="table-head">Delivery · payment</th>
              <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {r.suppliers.map((s) => (
              <tr key={s.id}>
                <td className="table-cell text-sm"><span className="font-semibold">{s.supplier.name}</span>
                  {s.their_reference ? <p className="text-xs text-gray-500">Their ref. {s.their_reference}{s.entered_by_supplier ? " · quoted on MakeSetu" : " · entered by us"}</p> : null}
                  {s.remarks ? <p className="text-xs text-gray-500">{s.remarks}</p> : null}</td>
                <td className="table-cell"><StatusBadge status={s.status === "invited" ? "awaiting" : s.status === "declined" || s.status === "not_awarded" ? "declined" : "accepted"} label={s.status_label} />
                  {s.purchase_order_id ? <p className="mt-1 text-xs"><Link to="/purchase/orders/$orderId" params={{ orderId: String(s.purchase_order_id) }} className="btn-link">{s.purchase_order_number}</Link></p> : null}</td>
                <td className="table-cell text-right text-sm">{s.lines_quoted} of {r.lines.length}{s.complete ? "" : s.lines_quoted ? <p className="text-xs text-amber-700">part</p> : null}</td>
                <td className="table-cell text-right text-sm">{s.lines_quoted ? rupees(s.total) : "—"}</td>
                <td className="table-cell text-sm">{s.delivery_days != null ? `${s.delivery_days} d` : "—"} · {s.payment_terms_days != null ? `${s.payment_terms_days} d credit` : "—"}
                  {s.valid_until ? <p className="text-xs text-gray-500">valid to {day(s.valid_until)}</p> : null}</td>
                <td className="table-cell text-right">{r.actions.enter_quotes ? <button type="button" className="btn-link" onClick={() => setEntering(s)}>{s.lines_quoted ? "Change quote" : "Enter quote"}</button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Comparison" description={r.actions.award ? "The lowest rate on each line is picked; change any, then award. Lines with no supplier chosen aren't ordered." : undefined} className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              {r.suppliers.map((s) => <th key={s.id} scope="col" className="table-head text-right">{s.supplier.name}</th>)}
              {r.actions.award ? <th scope="col" className="table-head">Award to</th> : null}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {r.lines.map(({ line, offers }) => (
              <tr key={line.id}>
                <td className="table-cell text-sm">{quantity(line.quantity, line.unit)} {line.item_name} <span className="text-gray-500">{line.item_code}</span>
                  {line.description ? <p className="text-xs text-gray-500">{line.description}</p> : null}
                  {line.awarded_to ? <Badge tone="green">Awarded to {name(line.awarded_to)}</Badge> : null}</td>
                {r.suppliers.map((s) => {
                  const o = offers.find((x) => x.invite_id === s.id);
                  return (
                    <td key={s.id} className={`table-cell text-right text-sm ${o?.lowest ? "font-semibold text-green-700" : ""}`}>
                      {o ? <>{rupees(o.rate)}<p className="text-xs text-gray-500">{rupees(o.value)} + {Number(o.gst_rate)}%</p></> : "—"}
                    </td>
                  );
                })}
                {r.actions.award ? (
                  <td className="table-cell">
                    <select className="field-input" aria-label={`Award ${line.item_name} to`} value={awardRows[line.id] ?? ""}
                      onChange={(e) => setAwards({ ...awards, [line.id]: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Don't order</option>
                      {offers.map((o) => <option key={o.invite_id} value={o.invite_id}>{name(o.invite_id)} · {rupees(o.rate)}</option>)}
                    </select>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        {r.actions.award ? (
          <div className="mt-4 flex items-center justify-end gap-4">
            <span className="text-sm">Awarded value before GST: <strong>{rupees(awardTotal)}</strong></span>
            <button type="button" className="btn-primary" disabled={award.isPending || !Object.values(awardRows).some((v) => v)}
              onClick={() => award.mutate(awardRows)}>Award and draft purchase orders</button>
          </div>
        ) : null}
      </Card>
      {r.notes ? <Card title="Notes for suppliers"><p className="whitespace-pre-line text-sm">{r.notes}</p></Card> : null}

      {entering ? <EnterQuote rfq={r} invite={entering} onClose={() => setEntering(null)} onSaved={refresh} /> : null}
      {adding ? <AddSupplier rfq={r} onClose={() => setAdding(false)} onSaved={refresh} /> : null}
      {cancelling ? (
        <ReasonModal title="Cancel this RFQ" label="Why? Suppliers asked will see it." button="Cancel RFQ" onClose={() => setCancelling(false)}
          send={async (reason) => { refresh(unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/cancel", { ...path, body: { reason } }))); setCancelling(false); }} />
      ) : null}
    </div>
  );
}

function EnterQuote({ rfq, invite, onClose, onSaved }: { rfq: Rfq; invite: Invite; onClose: () => void; onSaved: (r: Rfq) => void }) {
  const [rows, setRows] = useState(Object.fromEntries(rfq.lines.map(({ line, offers }) => {
    const o = offers.find((x) => x.invite_id === invite.id);
    return [line.id, { rate: o ? String(Number(o.rate)) : "", gst_rate: o ? String(Number(o.gst_rate)) : "" }];
  })));
  const [terms, setTerms] = useState<QuoteTerms>({
    their_reference: invite.their_reference, delivery_days: invite.delivery_days != null ? String(invite.delivery_days) : "",
    payment_terms_days: invite.payment_terms_days != null ? String(invite.payment_terms_days) : "", valid_until: invite.valid_until ?? "", remarks: invite.remarks,
  });
  const path = { params: { path: { rfq_id: rfq.id, invite_id: invite.id } } };
  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/suppliers/{invite_id}/quote", { ...path, body: quoteBody(rfq.lines.map((l) => l.line.id), rows, terms) })),
    onSuccess: (r) => { onSaved(r); onClose(); },
  });
  const declined = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/suppliers/{invite_id}/decline", { ...path, body: { reason: terms.remarks } })),
    onSuccess: (r) => { onSaved(r); onClose(); },
  });
  return (
    <Modal wide title={`Quote from ${invite.supplier.name}`} onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary mr-auto" onClick={() => declined.mutate()}>They won't quote</button>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save quote</button>
      </>
    }>
      <p className="text-sm text-gray-600">From their email or letter. Leave a rate blank for lines they didn't quote.</p>
      <QuoteFields lines={rfq.lines.map(({ line }) => ({ id: line.id, item_name: line.item_name, item_code: line.item_code, quantity: line.quantity, unit: line.unit }))}
        rows={rows} setRows={setRows} terms={terms} setTerms={setTerms} />
      <ErrorText error={save.error ?? declined.error} />
    </Modal>
  );
}

function AddSupplier({ rfq, onClose, onSaved }: { rfq: Rfq; onClose: () => void; onSaved: (r: Rfq) => void }) {
  const suppliers = useQuery({ queryKey: ["purchase-suppliers"], queryFn: async () => unwrap(await api.GET("/api/v1/purchase/suppliers")) });
  const asked = new Set(rfq.suppliers.map((s) => s.supplier.id));
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const add = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/rfqs/{rfq_id}/suppliers", { params: { path: { rfq_id: rfq.id } }, body: { supplier_id: supplierId! } })),
    onSuccess: (r) => { onSaved(r); onClose(); },
  });
  return (
    <Modal title="Ask another supplier" onClose={onClose} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!supplierId || add.isPending} onClick={() => add.mutate()}>Add</button>
      </>
    }>
      <Field label="Supplier">
        {(id) => (
          <select id={id} className="field-input" value={supplierId ?? ""} onChange={(e) => setSupplierId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Choose</option>
            {(suppliers.data ?? []).filter((s) => !asked.has(s.id)).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
      </Field>
      <ErrorText error={add.error} />
    </Modal>
  );
}

type Line = { key: number; item_id: number | null; quantity: string; description: string };
let nextKey = 1;
const blankLine = (): Line => ({ key: nextKey++, item_id: null, quantity: "", description: "" });

/** New (/purchase/rfqs/new) or a draft (/purchase/rfqs/:id/edit). */
export function RfqEditor() {
  const { rfqId } = useParams({ strict: false }) as { rfqId?: string };
  const existing = useRfq(rfqId ?? "0", !!rfqId);
  if (rfqId && existing.isLoading) return <Loading />;
  if (rfqId && existing.error) return <ErrorText error={existing.error} />;
  if (rfqId && existing.data && !existing.data.actions.edit) return <Empty title="This RFQ has been sent, so it can't be changed" />;
  return <RfqForm rfq={rfqId ? existing.data ?? null : null} />;
}

function RfqForm({ rfq }: { rfq: Rfq | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const suppliers = useQuery({ queryKey: ["purchase-suppliers"], queryFn: async () => unwrap(await api.GET("/api/v1/purchase/suppliers")) });
  const items = useItems();
  const warehouses = useWarehouses();
  const activeItems = (items.data ?? []).filter((i) => i.is_active);
  const stores = (warehouses.data?.warehouses ?? []).filter((w) => w.is_active);
  const [title, setTitle] = useState(rfq?.title ?? "");
  const [shipTo, setShipTo] = useState<number | null>(rfq?.ship_to_id ?? null);
  const [respondBy, setRespondBy] = useState(rfq?.respond_by ?? "");
  const [requiredBy, setRequiredBy] = useState(rfq?.required_by ?? "");
  const [notes, setNotes] = useState(rfq?.notes ?? "");
  const [chosen, setChosen] = useState<number[]>(rfq ? rfq.suppliers.map((s) => s.supplier.id) : []);
  const [lines, setLines] = useState<Line[]>(rfq ? rfq.lines.map(({ line }) => ({ key: nextKey++, item_id: line.item_id, quantity: String(Number(line.quantity)), description: line.description })) : [blankLine()]);
  const warehouseId = shipTo ?? stores.find((w) => w.is_default)?.id ?? stores[0]?.id ?? null;
  const setLine = (key: number, patch: Partial<Line>) => setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        title, ship_to_id: warehouseId!, rfq_date: rfq?.rfq_date ?? todayIso(), respond_by: respondBy || null, required_by: requiredBy || null, notes,
        supplier_ids: chosen,
        lines: lines.filter((l) => l.item_id !== null).map((l) => ({ item_id: l.item_id!, quantity: l.quantity, description: l.description })),
      };
      return rfq ? unwrap(await api.PUT("/api/v1/rfqs/{rfq_id}", { params: { path: { rfq_id: rfq.id } }, body }))
        : unwrap(await api.POST("/api/v1/rfqs", { body }));
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["rfqs"] });
      queryClient.setQueryData(rfqKey(saved.id), saved);
      navigate({ to: "/purchase/rfqs/$rfqId", params: { rfqId: String(saved.id) } });
    },
  });
  if (suppliers.isLoading || items.isLoading || warehouses.isLoading) return <Loading />;
  const ready = title.trim() && warehouseId !== null && lines.some((l) => l.item_id !== null && Number(l.quantity) > 0);
  return (
    <div className="space-y-6">
      <PageHeader title={rfq ? `Edit ${rfq.number}` : "New request for quotation"} description="Saved as a draft. Send it from the RFQ page; suppliers on MakeSetu get it straight away." />
      <Card>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2"><Field label="Title" hint="What it's for, e.g. 'EN8 bar for January'.">{(id) => <input id={id} className="field-input" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />}</Field></div>
          <Field label="Deliver to">
            {(id) => (
              <select id={id} className="field-input" value={warehouseId ?? ""} onChange={(e) => setShipTo(Number(e.target.value))}>
                {stores.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Quotes by">{(id) => <input id={id} type="date" className="field-input" value={respondBy} onChange={(e) => setRespondBy(e.target.value)} />}</Field>
          <Field label="Needed by">{(id) => <input id={id} type="date" className="field-input" value={requiredBy} onChange={(e) => setRequiredBy(e.target.value)} />}</Field>
        </div>
      </Card>
      <Card title="What you need" className="overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="border-b border-gray-100">
            <tr><th scope="col" className="table-head">Item and specification</th><th scope="col" className="table-head w-40">Quantity</th><th scope="col" className="table-head w-10"><span className="sr-only">Remove</span></th></tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.map((l, index) => {
              const item = activeItems.find((i) => i.id === l.item_id);
              return (
                <tr key={l.key}>
                  <td className="table-cell">
                    <select className="field-input" aria-label={`Item, line ${index + 1}`} value={l.item_id ?? ""} onChange={(e) => setLine(l.key, { item_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Choose an item</option>
                      {activeItems.map((i) => <option key={i.id} value={i.id}>{i.code} · {i.name}</option>)}
                    </select>
                    <input className="field-input mt-1 text-xs" placeholder="Specification, grade, drawing number…" aria-label={`Specification, line ${index + 1}`} value={l.description} maxLength={200} onChange={(e) => setLine(l.key, { description: e.target.value })} />
                  </td>
                  <td className="table-cell"><div className="flex items-center gap-1">
                    <input className="field-input" inputMode="decimal" aria-label={`Quantity, line ${index + 1}`} value={l.quantity} onChange={(e) => setLine(l.key, { quantity: e.target.value })} />
                    <span className="text-xs text-gray-500">{item?.unit}</span>
                  </div></td>
                  <td className="table-cell">{lines.length > 1 ? <button type="button" className="text-gray-400 hover:text-red-600" aria-label={`Remove line ${index + 1}`} onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>✕</button> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <button type="button" className="btn-link mt-3" onClick={() => setLines([...lines, blankLine()])}>+ Add a line</button>
      </Card>
      <Card title="Suppliers to ask" description="Up to 10. Suppliers not on MakeSetu can be sent the PDF.">
        {(suppliers.data ?? []).length === 0 ? <Empty title="No suppliers yet">Add suppliers under Partners first.</Empty> : (
          <div className="grid gap-2 sm:grid-cols-2">
            {(suppliers.data ?? []).map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={chosen.includes(s.id)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, s.id] : chosen.filter((x) => x !== s.id))} />
                {s.name}{s.gstin ? <span className="text-xs text-gray-500">{s.gstin}</span> : null}
              </label>
            ))}
          </div>
        )}
      </Card>
      <Card><Field label="Notes for suppliers" hint="Inspection, test certificates, packing…">{(id) => <textarea id={id} className="field-input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field></Card>
      <ErrorText error={save.error} />
      <div className="flex justify-end gap-3">
        <Link to={rfq ? "/purchase/rfqs/$rfqId" : "/purchase/rfqs"} params={rfq ? { rfqId: String(rfq.id) } : {}} className="btn-secondary">Cancel</Link>
        <button type="button" className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Save draft</button>
      </div>
    </div>
  );
}
