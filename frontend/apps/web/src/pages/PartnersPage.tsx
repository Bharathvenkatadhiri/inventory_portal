import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../components/ui";
import { rupees } from "../lib/format";
import { useCan, useWritable } from "../lib/session";

type Partner = Schemas["PartnerOut"];
type Relation = "supplier" | "customer";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "supplier", label: "Suppliers" },
  { key: "customer", label: "Customers" },
  { key: "requests", label: "Requests for you" },
  { key: "waiting", label: "Waiting on them" },
  { key: "blocked", label: "Blocked" },
] as const;
type Filter = (typeof FILTERS)[number]["key"];

function matches(partner: Partner, filter: Filter): boolean {
  switch (filter) {
    case "supplier":
    case "customer":
      return partner.relation === filter && partner.status !== "blocked";
    case "requests":
      return partner.waiting_on === "us";
    case "waiting":
      return partner.waiting_on === "them";
    case "blocked":
      return partner.status === "blocked";
    default:
      return true;
  }
}

function StatusBadge({ partner }: { partner: Partner }) {
  if (partner.status === "active") return <Badge tone="green">Active</Badge>;
  if (partner.status === "blocked") return <Badge tone="red">{partner.blocked_by_us ? "Blocked by you" : "Blocked by them"}</Badge>;
  if (partner.waiting_on === "us") {
    return <Badge tone="orange">{partner.relation === "supplier" ? "Wants to supply you" : "Wants to buy from you"}</Badge>;
  }
  return <Badge tone="amber">{partner.status === "pending_approval" ? "Waiting for their vendor approval" : "Waiting for them to accept"}</Badge>;
}

export function PartnersPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("partners.manage") && writable;
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Partner | null>(null);
  const partners = useQuery({ queryKey: ["partners"], queryFn: async () => unwrap(await api.GET("/api/v1/partners")) });

  const act = useMutation({
    mutationFn: async ({ id, action }: { id: number; action: "accept" | "decline" | "block" | "unblock" }) => {
      const params = { path: { relationship_id: id } };
      if (action === "accept") return unwrap(await api.POST("/api/v1/partners/{relationship_id}/accept", { params }));
      if (action === "decline") return unwrap(await api.POST("/api/v1/partners/{relationship_id}/decline", { params }));
      if (action === "block") return unwrap(await api.POST("/api/v1/partners/{relationship_id}/block", { params }));
      return unwrap(await api.POST("/api/v1/partners/{relationship_id}/unblock", { params }));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["partners"] }),
  });

  const list = (partners.data ?? []).filter((p) => matches(p, filter));
  const requests = (partners.data ?? []).filter((p) => p.waiting_on === "us").length;

  return (
    <div>
      <PageHeader
        title="Partners"
        description="Your customers and suppliers. On MakeSetu, a purchase order you raise to a connected supplier is the same record they see as an incoming order."
        actions={manage ? <button type="button" className="btn-primary" onClick={() => setAdding(true)}>Add a partner</button> : null}
      />

      <div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Show">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            role="tab"
            aria-selected={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded-full px-3 py-1.5 text-sm font-semibold ${filter === f.key ? "bg-navy-900 text-white" : "bg-white text-gray-600 ring-1 ring-gray-200 hover:text-navy-900"}`}
          >
            {f.label}
            {f.key === "requests" && requests > 0 ? <span className="ml-1.5 rounded-full bg-accent-500 px-1.5 text-xs text-white">{requests}</span> : null}
          </button>
        ))}
      </div>

      <ErrorText error={act.error} />
      <Card className="mt-3 overflow-x-auto p-0 sm:p-0">
        {partners.isLoading ? (
          <Loading />
        ) : partners.error ? (
          <div className="p-5"><ErrorText error={partners.error} /></div>
        ) : list.length === 0 ? (
          <div className="p-5">
            <Empty title={filter === "all" ? "No customers or suppliers yet" : "Nothing here"}>
              {filter === "all" && manage ? "Add a partner to find them on MakeSetu, or add one by GSTIN." : null}
            </Empty>
          </div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">Company</th>
                <th scope="col" className="table-head">They are</th>
                <th scope="col" className="table-head">Status</th>
                <th scope="col" className="table-head">Terms</th>
                <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {list.map((p) => (
                <tr key={p.id}>
                  <td className="table-cell">
                    <p className="font-semibold text-navy-900">{p.partner.name}</p>
                    <p className="text-xs text-gray-500">
                      {[p.partner.city, p.partner.state].filter(Boolean).join(", ")}
                      {p.partner.gstin ? ` · ${p.partner.gstin}` : ""}
                    </p>
                    {!p.partner.on_network ? <p className="mt-1 text-xs text-gray-500">Not on MakeSetu yet</p> : null}
                  </td>
                  <td className="table-cell">{p.relation === "supplier" ? "Supplier" : "Customer"}</td>
                  <td className="table-cell"><StatusBadge partner={p} /></td>
                  <td className="table-cell text-xs text-gray-600">
                    {p.payment_terms_days !== null && p.payment_terms_days !== undefined ? <p>{p.payment_terms_days} days to pay</p> : null}
                    {p.credit_limit ? <p>Credit {rupees(p.credit_limit)}</p> : null}
                    {p.relation === "supplier" && p.vendor_code ? <p>Vendor code {p.vendor_code}</p> : null}
                    {p.relation === "customer" && p.customer_code ? <p>Customer code {p.customer_code}</p> : null}
                  </td>
                  <td className="table-cell whitespace-nowrap text-right">
                    {manage ? (
                      <div className="flex justify-end gap-3">
                        {p.waiting_on === "us" ? (
                          <>
                            <button type="button" className="btn-link" onClick={() => act.mutate({ id: p.id, action: "accept" })}>
                              {p.relation === "supplier" ? "Approve vendor" : "Accept"}
                            </button>
                            <button type="button" className="btn-link text-gray-500" onClick={() => act.mutate({ id: p.id, action: "decline" })}>Decline</button>
                          </>
                        ) : null}
                        {p.waiting_on === "them" ? (
                          <button type="button" className="btn-link text-gray-500" onClick={() => act.mutate({ id: p.id, action: "decline" })}>Withdraw</button>
                        ) : null}
                        {p.status === "active" ? (
                          <>
                            <button type="button" className="btn-link" onClick={() => setEditing(p)}>Terms</button>
                            <button type="button" className="btn-link text-red-700" onClick={() => act.mutate({ id: p.id, action: "block" })}>Block</button>
                          </>
                        ) : null}
                        {p.status === "blocked" && p.blocked_by_us ? (
                          <button type="button" className="btn-link" onClick={() => act.mutate({ id: p.id, action: "unblock" })}>Unblock</button>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {adding ? <AddPartner onClose={() => setAdding(false)} /> : null}
      {editing ? <EditTerms partner={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function AddPartner({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [relation, setRelation] = useState<Relation>("supplier");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [gstin, setGstin] = useState("");
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const results = useQuery({
    queryKey: ["partner-search", search],
    enabled: search.length >= 3,
    queryFn: async () => unwrap(await api.GET("/api/v1/partners/search", { params: { query: { q: search } } })),
  });

  const connect = useMutation({
    mutationFn: async (target: { company_id?: number; gstin?: string; email?: string }) =>
      unwrap(await api.POST("/api/v1/partners", { body: { relation, ...target } })),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["partners"] });
      const name = result.partner.partner.name;
      setDone(
        result.partner.status === "active"
          ? `${name} is now one of your ${relation}s.${result.invite_email_sent ? " We emailed them an invitation to join MakeSetu." : ""}`
          : `Request sent to ${name}. They'll see it under Partners.`,
      );
    },
  });

  if (done) {
    return (
      <Modal title="Partner added" onClose={onClose} footer={<button type="button" className="btn-primary" onClick={onClose}>Done</button>}>
        <p className="text-sm text-gray-700">{done}</p>
      </Modal>
    );
  }

  return (
    <Modal title="Add a partner" onClose={onClose} wide>
      <fieldset>
        <legend className="field-label">They are your</legend>
        <div className="flex gap-2">
          {(["supplier", "customer"] as const).map((r) => (
            <label key={r} className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-sm font-semibold ${relation === r ? "border-accent-500 bg-accent-50" : "border-gray-200"}`}>
              <input type="radio" name="relation" className="sr-only" checked={relation === r} onChange={() => setRelation(r)} />
              {r === "supplier" ? "Supplier (you buy from them)" : "Customer (you sell to them)"}
            </label>
          ))}
        </div>
      </fieldset>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(query.trim());
        }}
      >
        <Field label="Find them on MakeSetu" hint="Search by company name or GSTIN.">
          {(id) => (
            <div className="flex gap-2">
              <input id={id} className="field-input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. Annai Precision" />
              <button type="submit" className="btn-secondary" disabled={query.trim().length < 3}>Search</button>
            </div>
          )}
        </Field>
      </form>

      {search.length >= 3 ? (
        results.isLoading ? <Loading label="Searching…" /> : results.error ? <ErrorText error={results.error} /> : (
          (results.data ?? []).length === 0 ? (
            <p className="text-sm text-gray-500">No company on MakeSetu matches “{search}”. Add them by GSTIN below.</p>
          ) : (
            <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
              {(results.data ?? []).map((r) => {
                const already = r.relationships.some((s) => s.startsWith(`${relation}:`));
                return (
                  <li key={r.id} className="flex items-center justify-between gap-3 px-3 py-2">
                    <div>
                      <p className="text-sm font-semibold text-navy-900">{r.name}</p>
                      <p className="text-xs text-gray-500">{r.legal_name} · {[r.city, r.state].filter(Boolean).join(", ")}</p>
                    </div>
                    {already ? (
                      <Badge>Already added</Badge>
                    ) : (
                      <button type="button" className="btn-secondary py-1" disabled={connect.isPending} onClick={() => connect.mutate({ company_id: r.id })}>
                        Add as {relation}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        )
      ) : null}

      <form
        className="space-y-3 border-t border-gray-100 pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          connect.mutate({ gstin: gstin.trim(), email: email.trim() || undefined });
        }}
      >
        <p className="text-sm font-semibold text-navy-900">Not on MakeSetu? Add them by GSTIN</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="GSTIN" hint="We fill in their legal name and address from GST.">
            {(id) => <input id={id} className="field-input font-mono uppercase" maxLength={15} value={gstin} onChange={(e) => setGstin(e.target.value)} placeholder="33ABCDE1234F1Z5" />}
          </Field>
          <Field label="Their email (optional)" hint="We'll invite them to join MakeSetu.">
            {(id) => <input id={id} type="email" className="field-input" value={email} onChange={(e) => setEmail(e.target.value)} />}
          </Field>
        </div>
        <ErrorText error={connect.error} />
        <div className="flex justify-end">
          <button type="submit" className="btn-primary" disabled={gstin.trim().length !== 15 || connect.isPending}>
            {connect.isPending ? "Adding…" : `Add as ${relation}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditTerms({ partner, onClose }: { partner: Partner; onClose: () => void }) {
  const queryClient = useQueryClient();
  const editable = new Set(partner.editable_terms);
  const [days, setDays] = useState(partner.payment_terms_days?.toString() ?? "");
  const [credit, setCredit] = useState(partner.credit_limit ?? "");
  const [vendorCode, setVendorCode] = useState(partner.vendor_code);
  const [customerCode, setCustomerCode] = useState(partner.customer_code);
  const save = useMutation({
    mutationFn: async () => {
      const body: Schemas["TermsIn"] = { payment_terms_days: days === "" ? null : Number(days) };
      if (editable.has("credit_limit")) body.credit_limit = credit === "" ? null : credit;
      if (editable.has("vendor_code")) body.vendor_code = vendorCode;
      if (editable.has("customer_code")) body.customer_code = customerCode;
      return unwrap(await api.PATCH("/api/v1/partners/{relationship_id}", { params: { path: { relationship_id: partner.id } }, body }));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["partners"] });
      onClose();
    },
  });
  return (
    <Modal
      title={`Terms with ${partner.partner.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <Field label="Payment terms (days)" hint="Agreed between you; either side can change it.">
        {(id) => <input id={id} type="number" min={0} max={365} className="field-input" value={days} onChange={(e) => setDays(e.target.value)} />}
      </Field>
      {editable.has("vendor_code") ? (
        <Field label="Your vendor code for them">
          {(id) => <input id={id} className="field-input" maxLength={30} value={vendorCode} onChange={(e) => setVendorCode(e.target.value)} />}
        </Field>
      ) : null}
      {editable.has("customer_code") ? (
        <Field label="Your customer code for them">
          {(id) => <input id={id} className="field-input" maxLength={30} value={customerCode} onChange={(e) => setCustomerCode(e.target.value)} />}
        </Field>
      ) : null}
      {editable.has("credit_limit") ? (
        <Field label="Credit limit you give them (₹)">
          {(id) => <input id={id} inputMode="decimal" className="field-input" value={credit} onChange={(e) => setCredit(e.target.value)} />}
        </Field>
      ) : null}
      <ErrorText error={save.error} />
    </Modal>
  );
}
