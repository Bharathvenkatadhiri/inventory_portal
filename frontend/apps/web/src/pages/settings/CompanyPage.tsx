import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader, Toggle } from "../../components/ui";
import { meKey, useCan, useWritable } from "../../lib/session";

type Company = Schemas["CompanyOut"];
type Address = Schemas["AddressOut"];
type BankAccount = Schemas["BankAccountOut"];

const ADDRESS_KINDS = [
  ["registered", "Registered office"],
  ["billing", "Billing"],
  ["shipping", "Shipping"],
  ["works", "Works / factory"],
  ["warehouse", "Warehouse"],
] as const;
const kindLabel = (kind: string) => ADDRESS_KINDS.find(([k]) => k === kind)?.[1] ?? kind;

export function CompanyPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("org.manage") && writable;
  const company = useQuery({ queryKey: ["organization"], queryFn: async () => unwrap(await api.GET("/api/v1/organization")) });

  return (
    <div className="space-y-6">
      <PageHeader title="Company" description="Your company registers once on MakeSetu. Its legal identity comes from its GST registration." />
      {company.isLoading ? <Loading /> : company.error ? <ErrorText error={company.error} /> : company.data ? (
        <>
          <Profile company={company.data} manage={manage} />
          <Addresses manage={manage} />
          {can("org.manage") ? <BankAccounts manage={manage} /> : null}
        </>
      ) : null}
    </div>
  );
}

function Profile({ company, manage }: { company: Company; manage: boolean }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(company.name);
  const update = useMutation({
    mutationFn: async (body: Schemas["OrganizationIn"]) => unwrap(await api.PATCH("/api/v1/organization", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(["organization"], data);
      queryClient.invalidateQueries({ queryKey: meKey });
      queryClient.invalidateQueries({ queryKey: ["permissions"] });
    },
  });
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card title="Profile">
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Legal name</dt><dd className="font-medium">{company.legal_name}</dd></div>
          <div><dt className="text-xs text-gray-500">GSTIN</dt><dd className="font-mono font-medium">{company.gstin || "—"}</dd></div>
          <div><dt className="text-xs text-gray-500">PAN</dt><dd className="font-mono font-medium">{company.pan || "—"}</dd></div>
          <div><dt className="text-xs text-gray-500">GST status</dt><dd className="font-medium">{company.gst_status || "—"}</dd></div>
          <div><dt className="text-xs text-gray-500">State</dt><dd className="font-medium">{company.state}{company.state_code ? ` (${company.state_code})` : ""}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Principal place of business</dt><dd className="font-medium">{company.principal_address || "—"}</dd></div>
        </dl>
        <form
          className="mt-5 flex items-end gap-2 border-t border-gray-100 pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate({ name });
          }}
        >
          <div className="flex-1">
            <Field label="Name on MakeSetu" hint="How customers and suppliers see you. Documents use the legal name.">
              {(id) => <input id={id} className="field-input" value={name} disabled={!manage} onChange={(e) => setName(e.target.value)} />}
            </Field>
          </div>
          {manage ? <button type="submit" className="btn-secondary mb-5" disabled={update.isPending || name === company.name}>Save</button> : null}
        </form>
      </Card>

      <Card title="How you use MakeSetu">
        <div className="space-y-5">
          <Toggle
            label="Buying"
            description="Purchase orders, goods receipts and purchase bills, and adding suppliers."
            checked={company.buying_enabled}
            disabled={!manage || update.isPending}
            onChange={(value) => update.mutate({ buying_enabled: value })}
          />
          <Toggle
            label="Selling"
            description="Quotations, sales orders, invoices and dispatch, and adding customers."
            checked={company.selling_enabled}
            disabled={!manage || update.isPending}
            onChange={(value) => update.mutate({ selling_enabled: value })}
          />
          <Toggle
            label="Findable by name"
            description="Other companies can find you by name when they add a partner. Off: only someone with your GSTIN can."
            checked={company.findable}
            disabled={!manage || update.isPending}
            onChange={(value) => update.mutate({ findable: value })}
          />
        </div>
        <div className="mt-4"><ErrorText error={update.error} /></div>
      </Card>
    </div>
  );
}

function Addresses({ manage }: { manage: boolean }) {
  const [editing, setEditing] = useState<Address | "new" | null>(null);
  const addresses = useQuery({ queryKey: ["addresses"], queryFn: async () => unwrap(await api.GET("/api/v1/organization/addresses")) });
  return (
    <Card
      title="Addresses"
      description="Where you bill from, ship from, and make things. Places of supply on GST documents come from these."
      actions={manage ? <button type="button" className="btn-secondary" onClick={() => setEditing("new")}>Add an address</button> : null}
    >
      {addresses.isLoading ? <Loading /> : (addresses.data ?? []).length === 0 ? (
        <Empty title="No addresses yet" />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {(addresses.data ?? []).map((a) => (
            <li key={a.id} className="rounded-lg border border-gray-200 p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <p className="font-semibold text-navy-900">{a.label || kindLabel(a.kind)}</p>
                <div className="flex items-center gap-2">
                  {a.is_default ? <Badge tone="navy">Default</Badge> : null}
                  {manage ? <button type="button" className="btn-link" onClick={() => setEditing(a)}>Edit</button> : null}
                </div>
              </div>
              <p className="mt-1 text-gray-600">{[a.line1, a.line2, a.city, a.state, a.pincode].filter(Boolean).join(", ")}</p>
              {a.label ? <p className="mt-1 text-xs text-gray-500">{kindLabel(a.kind)}</p> : null}
            </li>
          ))}
        </ul>
      )}
      {editing ? <AddressForm address={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </Card>
  );
}

function AddressForm({ address, onClose }: { address: Address | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Schemas["AddressIn"]>(
    address ?? { kind: "works", label: "", line1: "", line2: "", city: "", state: "", state_code: "", pincode: "", is_default: false },
  );
  const set = (field: keyof Schemas["AddressIn"]) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [field]: event.target.type === "checkbox" ? (event.target as HTMLInputElement).checked : event.target.value });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["addresses"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () =>
      address
        ? unwrap(await api.PUT("/api/v1/organization/addresses/{address_id}", { params: { path: { address_id: address.id } }, body: form }))
        : unwrap(await api.POST("/api/v1/organization/addresses", { body: form })),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(await api.DELETE("/api/v1/organization/addresses/{address_id}", { params: { path: { address_id: address!.id } } })),
    onSuccess: refresh,
  });
  return (
    <Modal
      title={address ? "Edit address" : "Add an address"}
      onClose={onClose}
      footer={
        <>
          {address ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="What it's for">
          {(id) => (
            <select id={id} className="field-input" value={form.kind} onChange={set("kind")}>
              {ADDRESS_KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Label (optional)" hint="e.g. Unit 2">
          {(id) => <input id={id} className="field-input" value={form.label} onChange={set("label")} />}
        </Field>
      </div>
      <Field label="Address">{(id) => <input id={id} className="field-input" value={form.line1} onChange={set("line1")} />}</Field>
      <Field label="Address line 2 (optional)">{(id) => <input id={id} className="field-input" value={form.line2} onChange={set("line2")} />}</Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="City">{(id) => <input id={id} className="field-input" value={form.city} onChange={set("city")} />}</Field>
        <Field label="State">{(id) => <input id={id} className="field-input" value={form.state} onChange={set("state")} />}</Field>
        <Field label="GST state code" hint="Two digits, e.g. 33 for Tamil Nadu.">
          {(id) => <input id={id} className="field-input" maxLength={2} inputMode="numeric" value={form.state_code} onChange={set("state_code")} />}
        </Field>
        <Field label="PIN code">{(id) => <input id={id} className="field-input" maxLength={6} inputMode="numeric" value={form.pincode} onChange={set("pincode")} />}</Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={form.is_default} onChange={set("is_default")} />
        Default {kindLabel(form.kind).toLowerCase()} address
      </label>
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}

function maskAccount(number: string): string {
  return number.length > 4 ? `•••• ${number.slice(-4)}` : number;
}

function BankAccounts({ manage }: { manage: boolean }) {
  const [editing, setEditing] = useState<BankAccount | "new" | null>(null);
  const accounts = useQuery({ queryKey: ["bank-accounts"], queryFn: async () => unwrap(await api.GET("/api/v1/organization/bank-accounts")) });
  return (
    <Card
      title="Bank accounts"
      description="Printed on your invoices for customers to pay into. Only people who manage company settings see these."
      actions={manage ? <button type="button" className="btn-secondary" onClick={() => setEditing("new")}>Add a bank account</button> : null}
    >
      {accounts.isLoading ? <Loading /> : (accounts.data ?? []).length === 0 ? (
        <Empty title="No bank accounts yet" />
      ) : (
        <ul className="divide-y divide-gray-100">
          {(accounts.data ?? []).map((a) => (
            <li key={a.id} className="flex items-center justify-between gap-3 py-3 text-sm">
              <div>
                <p className="font-semibold text-navy-900">{a.bank_name} {a.is_default ? <Badge tone="navy">Default</Badge> : null}</p>
                <p className="text-gray-600">{a.account_name} · <span className="font-mono">{maskAccount(a.account_number)}</span> · {a.ifsc}</p>
              </div>
              {manage ? <button type="button" className="btn-link" onClick={() => setEditing(a)}>Edit</button> : null}
            </li>
          ))}
        </ul>
      )}
      {editing ? <BankAccountForm account={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </Card>
  );
}

function BankAccountForm({ account, onClose }: { account: BankAccount | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Schemas["BankAccountIn"]>(
    account ?? { account_name: "", bank_name: "", account_number: "", ifsc: "", branch: "", is_default: false },
  );
  const set = (field: keyof Schemas["BankAccountIn"]) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [field]: event.target.type === "checkbox" ? event.target.checked : event.target.value });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["bank-accounts"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () =>
      account
        ? unwrap(await api.PUT("/api/v1/organization/bank-accounts/{account_id}", { params: { path: { account_id: account.id } }, body: form }))
        : unwrap(await api.POST("/api/v1/organization/bank-accounts", { body: form })),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(await api.DELETE("/api/v1/organization/bank-accounts/{account_id}", { params: { path: { account_id: account!.id } } })),
    onSuccess: refresh,
  });
  return (
    <Modal
      title={account ? "Edit bank account" : "Add a bank account"}
      onClose={onClose}
      footer={
        <>
          {account ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <Field label="Account name">{(id) => <input id={id} className="field-input" value={form.account_name} onChange={set("account_name")} />}</Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Bank">{(id) => <input id={id} className="field-input" value={form.bank_name} onChange={set("bank_name")} />}</Field>
        <Field label="Branch (optional)">{(id) => <input id={id} className="field-input" value={form.branch} onChange={set("branch")} />}</Field>
        <Field label="Account number">{(id) => <input id={id} className="field-input font-mono" inputMode="numeric" value={form.account_number} onChange={set("account_number")} />}</Field>
        <Field label="IFSC">{(id) => <input id={id} className="field-input font-mono uppercase" maxLength={11} value={form.ifsc} onChange={set("ifsc")} />}</Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={form.is_default} onChange={set("is_default")} />
        Default account for invoices
      </label>
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
