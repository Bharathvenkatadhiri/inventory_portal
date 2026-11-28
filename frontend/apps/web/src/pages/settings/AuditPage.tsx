import { api, unwrap, type Schemas } from "@makesetu/api";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import { Badge, Card, Empty, ErrorText, Loading, PageHeader } from "../../components/ui";

type Event = Schemas["AuditEventOut"];

const TONE: Record<string, "green" | "navy" | "red"> = { create: "green", update: "navy", delete: "red" };

function show(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function label(field: string) {
  return field.replace(/_id$/, "").replace(/_/g, " ");
}

/** Settings > Audit log: who changed what, newest first. */
export function AuditPage() {
  const [objectType, setObjectType] = useState("");
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const log = useInfiniteQuery({
    queryKey: ["audit", objectType, action, q],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/api/v1/audit", {
      params: { query: { object_type: objectType, action, q, ...(pageParam ? { before_id: pageParam } : {}) } },
    })),
    getNextPageParam: (last) => (last.more && last.events.length ? last.events[last.events.length - 1].id : undefined),
  });
  const events: Event[] = (log.data?.pages ?? []).flatMap((p) => p.events);
  const types = log.data?.pages[0]?.object_types ?? [];
  return (
    <div className="space-y-6">
      <PageHeader title="Audit log" description="Every change to documents, masters and settings: who made it, when, from which IP address, and what changed. Entries can't be edited or deleted." />
      <div className="flex flex-wrap gap-3">
        <select className="field-input w-56" aria-label="What" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
          <option value="">Everything</option>
          {types.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select>
        <select className="field-input w-40" aria-label="Change" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Any change</option>
          <option value="create">Created</option>
          <option value="update">Changed</option>
          <option value="delete">Deleted</option>
        </select>
        <input className="field-input w-64" placeholder="Document number or person" aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <Card className="overflow-x-auto p-0">
        {log.isLoading ? <Loading /> : log.error ? <div className="p-5"><ErrorText error={log.error} /></div> : events.length === 0 ? (
          <div className="p-5"><Empty title="Nothing recorded yet" /></div>
        ) : (
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-gray-100">
              <tr>
                <th scope="col" className="table-head">When</th>
                <th scope="col" className="table-head">Who</th>
                <th scope="col" className="table-head">What</th>
                <th scope="col" className="table-head">Change</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {events.map((e) => {
                const changes = (e.changes ?? {}) as Record<string, unknown>;
                const fields = Object.keys(changes);
                return (
                  <Fragment key={e.id}>
                    <tr className="cursor-pointer hover:bg-gray-50" onClick={() => setOpen(open === e.id ? null : e.id)}>
                      <td className="table-cell text-sm">{new Date(e.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</td>
                      <td className="table-cell text-sm">{e.user_name}{e.ip_address ? <p className="text-xs text-gray-500">{e.ip_address}</p> : null}</td>
                      <td className="table-cell text-sm">{e.object_name} <span className="font-semibold">{e.object_label}</span></td>
                      <td className="table-cell text-sm">
                        <Badge tone={TONE[e.action] ?? "gray"}>{e.action_label}</Badge>
                        {e.action === "update" ? <span className="ml-2 text-xs text-gray-500">{fields.map(label).join(", ")}</span> : null}
                      </td>
                    </tr>
                    {open === e.id ? (
                      <tr>
                        <td colSpan={4} className="bg-gray-50 px-4 py-2">
                          <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                            {fields.map((f) => {
                              const v = changes[f];
                              return (
                                <div key={f} className="flex gap-2">
                                  <dt className="w-40 shrink-0 text-gray-500">{label(f)}</dt>
                                  <dd className="break-all">{Array.isArray(v) && e.action === "update" ? <>{show(v[0])} → <strong>{show(v[1])}</strong></> : show(Array.isArray(v) ? v.join(", ") : v)}</dd>
                                </div>
                              );
                            })}
                          </dl>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      {log.hasNextPage ? <button type="button" className="btn-secondary" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>Older</button> : null}
    </div>
  );
}
