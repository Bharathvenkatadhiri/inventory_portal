import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, ErrorText, Field, Loading, Modal, PageHeader } from "../../components/ui";
import { plural } from "../../lib/format";
import { meKey, useCan, useWritable } from "../../lib/session";

type Role = Schemas["RoleOut"];
type Permission = Schemas["PermissionOut"];

export function RolesPage() {
  const can = useCan();
  const writable = useWritable();
  const manage = can("roles.manage") && writable;
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const roles = useQuery({ queryKey: ["roles"], queryFn: async () => unwrap(await api.GET("/api/v1/roles")) });
  const permissions = useQuery({ queryKey: ["permissions"], queryFn: async () => unwrap(await api.GET("/api/v1/organization/permissions")) });
  if (roles.isLoading || permissions.isLoading) return <Loading />;
  if (roles.error || permissions.error) return <ErrorText error={roles.error ?? permissions.error} />;
  const labels = new Map((permissions.data ?? []).map((p) => [p.code, p.label]));

  const group = (type: "full" | "light", title: string, description: string) => (
    <Card title={title} description={description}>
      <ul className="grid gap-3 md:grid-cols-2">
        {(roles.data ?? []).filter((r) => r.user_type === type).map((r) => (
          <li key={r.id} className="rounded-lg border border-gray-200 p-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-navy-900">{r.name}</p>
                <p className="text-sm text-gray-500">{r.description}</p>
              </div>
              {manage && !r.locked ? <button type="button" className="btn-link shrink-0" onClick={() => setEditing(r)}>Edit</button> : null}
            </div>
            <p className="mt-2 text-xs text-gray-500">
              {plural(r.member_count, "person", "people")}
              {r.locked ? " · always has every permission" : ` · ${plural(r.permissions.length, "permission")}`}
              {!r.is_system ? " · your own role" : ""}
            </p>
            {!r.locked && r.permissions.length > 0 ? (
              <p className="mt-2 text-xs text-gray-600">{r.permissions.map((c) => labels.get(c) ?? c).join(" · ")}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Roles"
        description="A role is a set of permissions. Everyone in your company has one role here; teams don't change what people can do."
        actions={manage ? <button type="button" className="btn-primary" onClick={() => setEditing("new")}>Create a role</button> : null}
      />
      {group("full", "Full users", "Office staff: purchase, sales, accounts, planning and admin.")}
      {group("light", "Light users", "Shop-floor and support staff who record one or two things. They can't approve anything or see finance.")}
      {editing ? <RoleEditor role={editing === "new" ? null : editing} permissions={permissions.data ?? []} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

export function RoleEditor({ role, permissions, onClose }: { role: Role | null; permissions: Permission[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [userType, setUserType] = useState<"full" | "light">((role?.user_type as "full" | "light") ?? "full");
  const [codes, setCodes] = useState<string[]>(role?.permissions ?? []);
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["roles"] });
    queryClient.invalidateQueries({ queryKey: meKey });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () =>
      role
        ? unwrap(await api.PATCH("/api/v1/roles/{role_id}", { params: { path: { role_id: role.id } }, body: { name, description, permissions: codes } }))
        : unwrap(await api.POST("/api/v1/roles", { body: { name, description, user_type: userType, permissions: codes } })),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/roles/{role_id}", { params: { path: { role_id: role!.id } } })),
    onSuccess: done,
  });

  const modules = [...new Set(permissions.map((p) => p.module))];
  const light = userType === "light";
  const toggle = (code: string) => setCodes(codes.includes(code) ? codes.filter((c) => c !== code) : [...codes, code]);

  return (
    <Modal
      title={role ? `Edit ${role.name}` : "Create a role"}
      onClose={onClose}
      wide
      footer={
        <>
          {role && !role.is_system ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete role</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">{(id) => <input id={id} className="field-input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Who it's for">
          {(id) => (
            <select
              id={id}
              className="field-input"
              value={userType}
              disabled={Boolean(role)}
              onChange={(e) => {
                const next = e.target.value as "full" | "light";
                setUserType(next);
                if (next === "light") setCodes(codes.filter((c) => permissions.find((p) => p.code === c)?.light_allowed));
              }}
            >
              <option value="full">Full users (office staff)</option>
              <option value="light">Light users (shop floor)</option>
            </select>
          )}
        </Field>
      </div>
      <Field label="Description">{(id) => <input id={id} className="field-input" value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>

      <div className="space-y-4">
        {modules.map((module) => (
          <fieldset key={module}>
            <legend className="mb-1 text-xs font-bold uppercase tracking-wide text-gray-500">{module}</legend>
            <div className="grid gap-1 sm:grid-cols-2">
              {permissions.filter((p) => p.module === module).map((p) => {
                const blocked = light && !p.light_allowed;
                return (
                  <label key={p.code} className={`flex items-start gap-2 rounded px-1 py-1 text-sm ${blocked ? "text-gray-400" : ""}`}>
                    <input
                      type="checkbox"
                      className="mt-0.5 rounded border-gray-300 text-accent-500 disabled:opacity-40"
                      checked={codes.includes(p.code)}
                      disabled={blocked}
                      onChange={() => toggle(p.code)}
                    />
                    <span>
                      {p.label}
                      {blocked ? <span className="ml-1 text-xs">(full users only)</span> : null}
                      {!blocked && !p.included && p.needs_plan ? <span className="ml-1"><Badge tone="orange">{p.needs_plan}</Badge></span> : null}
                      {!blocked && p.included && !p.side_enabled ? <span className="ml-1 text-xs text-gray-500">({p.side} is off)</span> : null}
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
      <p className="text-xs text-gray-500">Permissions marked with a plan name only work once your company is on that plan.</p>
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
