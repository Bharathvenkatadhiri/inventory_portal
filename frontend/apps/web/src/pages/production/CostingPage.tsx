import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { quantity, rupees } from "../../lib/format";
import { useWritable } from "../../lib/session";
import { todayIso } from "../purchase/PurchaseLayout";

type Machine = Schemas["MachineOut"];
type Sheet = Schemas["CostSheetOut"];

const PART_LABELS: Record<string, string> = {
  material: "Materials", labour: "Labour", machine: "Machine time", overhead: "Overheads", scrap: "Less scrap recovered", total: "Total",
};

/** Standard against actual, by cost part. Shared with the work order page. */
export function CostSheetTable({ sheet }: { sheet: Sheet }) {
  return (
    <table className="w-full">
      <thead className="border-b border-gray-100">
        <tr>
          <th scope="col" className="table-head">For {quantity(sheet.good)} good</th>
          <th scope="col" className="table-head text-right">At standard</th>
          <th scope="col" className="table-head text-right">Actual</th>
          <th scope="col" className="table-head text-right">Variance</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {sheet.rows.map((r) => {
          // More cost than standard is bad, except for scrap, where more recovered is good.
          const worse = r.part === "scrap" ? Number(r.variance) < 0 : Number(r.variance) > 0;
          return (
            <tr key={r.part} className={r.part === "total" ? "font-semibold" : ""}>
              <td className="table-cell text-sm">{PART_LABELS[r.part] ?? r.part}</td>
              <td className="table-cell text-right text-sm">{rupees(r.standard)}</td>
              <td className="table-cell text-right text-sm">{rupees(r.actual)}</td>
              <td className={`table-cell text-right text-sm ${Number(r.variance) === 0 ? "" : worse ? "text-red-700" : "text-green-700"}`}>
                {Number(r.variance) > 0 ? "+" : ""}{rupees(r.variance)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function CostingPage() {
  const writable = useWritable();
  return (
    <div className="space-y-6">
      <PageHeader
        title="Production costing"
        description="What an hour of labour and of each machine costs, and overheads. Production entries add these to the cost of what's made (less any scrap recovered), so stock is valued at full cost."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <RatesCard writable={writable} />
        <MachinesCard writable={writable} />
      </div>
      <CostReport />
    </div>
  );
}

function RatesCard({ writable }: { writable: boolean }) {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["cost-settings"], queryFn: async () => unwrap(await api.GET("/api/v1/production/cost-settings")) });
  const [labour, setLabour] = useState("");
  const [overhead, setOverhead] = useState("");
  useEffect(() => {
    if (settings.data) {
      setLabour(String(Number(settings.data.labour_hour_rate)));
      setOverhead(String(Number(settings.data.overhead_percent)));
    }
  }, [settings.data]);
  const save = useMutation({
    mutationFn: async () => unwrap(await api.PUT("/api/v1/production/cost-settings", { body: { labour_hour_rate: labour || "0", overhead_percent: overhead || "0" } })),
    onSuccess: () => { for (const key of ["cost-settings", "boms"]) queryClient.invalidateQueries({ queryKey: [key] }); },
  });
  return (
    <Card title="Rates">
      {settings.isLoading ? <Loading /> : (
        <div className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Labour (₹ per person-hour)" hint="Wages and benefits over hours worked.">
              {(id) => <input id={id} className="field-input" inputMode="decimal" value={labour} disabled={!writable} onChange={(e) => setLabour(e.target.value)} />}
            </Field>
            <Field label="Overheads (% of labour and machine)" hint="Power, supervision, rent…">
              {(id) => <input id={id} className="field-input" inputMode="decimal" value={overhead} disabled={!writable} onChange={(e) => setOverhead(e.target.value)} />}
            </Field>
          </div>
          {writable ? <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isSuccess ? "Saved" : "Save rates"}</button> : null}
          <ErrorText error={save.error} />
        </div>
      )}
    </Card>
  );
}

function MachinesCard({ writable }: { writable: boolean }) {
  const machines = useQuery({ queryKey: ["machines"], queryFn: async () => unwrap(await api.GET("/api/v1/machines")) });
  const [editing, setEditing] = useState<Machine | "new" | null>(null);
  return (
    <Card title="Machines and work centres" actions={writable ? <button type="button" className="btn-secondary" onClick={() => setEditing("new")}>Add machine</button> : null}>
      {machines.isLoading ? <Loading /> : (machines.data ?? []).length === 0 ? <Empty title="No machines yet">Add each machine or work centre with its hour rate.</Empty> : (
        <ul className="divide-y divide-gray-100">
          {(machines.data ?? []).map((m) => (
            <li key={m.id} className="flex items-center justify-between py-2 text-sm">
              <span><span className="font-semibold">{m.code}</span> {m.name} {!m.is_active ? <Badge>Inactive</Badge> : null}</span>
              <span className="flex items-center gap-3">
                {m.hour_rate != null ? `${rupees(m.hour_rate)}/h` : null}
                {writable ? <button type="button" className="btn-link" onClick={() => setEditing(m)}>Edit</button> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      {editing ? <MachineForm machine={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </Card>
  );
}

function MachineForm({ machine, onClose }: { machine: Machine | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState(machine?.code ?? "");
  const [name, setName] = useState(machine?.name ?? "");
  const [rate, setRate] = useState(machine?.hour_rate != null ? String(Number(machine.hour_rate)) : "");
  const [active, setActive] = useState(machine?.is_active ?? true);
  const done = () => { for (const key of ["machines", "boms"]) queryClient.invalidateQueries({ queryKey: [key] }); onClose(); };
  const save = useMutation({
    mutationFn: async () => {
      const body = { code, name, hour_rate: rate || "0", is_active: active };
      return machine ? unwrap(await api.PUT("/api/v1/machines/{machine_id}", { params: { path: { machine_id: machine.id } }, body }))
        : unwrap(await api.POST("/api/v1/machines", { body }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/machines/{machine_id}", { params: { path: { machine_id: machine!.id } } })),
    onSuccess: done,
  });
  return (
    <Modal title={machine ? `Edit ${machine.code}` : "Add a machine"} onClose={onClose} footer={
      <>
        {machine && !machine.used ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete</button> : null}
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!code.trim() || !name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
      </>
    }>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Code">{(id) => <input id={id} className="field-input uppercase" value={code} maxLength={20} onChange={(e) => setCode(e.target.value)} />}</Field>
        <div className="sm:col-span-2"><Field label="Name">{(id) => <input id={id} className="field-input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}</Field></div>
      </div>
      <Field label="Hour rate (₹)" hint="Depreciation, power and maintenance for an hour of running.">
        {(id) => <input id={id} className="field-input" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />}
      </Field>
      {machine ? (
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="rounded border-gray-300 text-accent-500" checked={active} onChange={(e) => setActive(e.target.checked)} />Active</label>
      ) : null}
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}

function monthStart() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1, 12).toISOString().slice(0, 10);
}

function CostReport() {
  const [start, setStart] = useState(monthStart());
  const [end, setEnd] = useState(todayIso());
  const [open, setOpen] = useState<number | null>(null);
  const report = useQuery({
    queryKey: ["production-costs", start, end],
    enabled: !!start && !!end,
    queryFn: async () => unwrap(await api.GET("/api/v1/production/costs", { params: { query: { start, end } } })),
  });
  const rows = report.data ?? [];
  return (
    <Card title="Cost of production" description="Actual against standard for each item made, with yield and scrap." actions={
      <div className="flex items-center gap-2 text-sm">
        <input type="date" className="field-input" aria-label="From" value={start} onChange={(e) => setStart(e.target.value)} />
        <span>to</span>
        <input type="date" className="field-input" aria-label="To" value={end} max={todayIso()} onChange={(e) => setEnd(e.target.value)} />
      </div>
    } className="overflow-x-auto">
      {report.isLoading ? <Loading /> : report.error ? <ErrorText error={report.error} /> : rows.length === 0 ? <Empty title="Nothing made in these dates" /> : (
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Item</th>
              <th scope="col" className="table-head text-right">Good · yield</th>
              <th scope="col" className="table-head text-right">Scrap</th>
              <th scope="col" className="table-head text-right">Standard a unit</th>
              <th scope="col" className="table-head text-right">Actual a unit</th>
              <th scope="col" className="table-head text-right">Variance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((r) => {
              const total = r.rows.find((x) => x.part === "total");
              const variance = Number(total?.variance ?? 0);
              return (
                <Fragment key={r.item_id}>
                  <tr className="cursor-pointer hover:bg-gray-50" onClick={() => setOpen(open === r.item_id ? null : r.item_id)}>
                    <td className="table-cell text-sm"><button type="button" className="font-semibold text-navy-900 hover:underline" aria-expanded={open === r.item_id}>{r.item_name}</button><p className="text-xs text-gray-500">{r.item_code} · {r.entries} entr{r.entries === 1 ? "y" : "ies"}</p></td>
                    <td className="table-cell text-right text-sm">{quantity(r.good, r.unit)}{r.yield_percent != null ? <p className="text-xs text-gray-500">{Number(r.yield_percent)}% yield</p> : null}</td>
                    <td className="table-cell text-right text-sm">{Number(r.scrap_quantity) ? quantity(r.scrap_quantity) : "—"}</td>
                    <td className="table-cell text-right text-sm">{rupees(r.standard_unit_cost)}</td>
                    <td className="table-cell text-right text-sm">{r.actual_unit_cost != null ? rupees(r.actual_unit_cost) : "—"}</td>
                    <td className={`table-cell text-right text-sm ${variance > 0 ? "text-red-700" : variance < 0 ? "text-green-700" : ""}`}>{variance > 0 ? "+" : ""}{rupees(variance)}</td>
                  </tr>
                  {open === r.item_id ? (
                    <tr><td colSpan={6} className="bg-gray-50 px-4 py-2"><CostSheetTable sheet={r} /></td></tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
