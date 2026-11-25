import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, Empty, ErrorText, Field, Loading, Meter, Modal, PageHeader } from "../../components/ui";
import { day, rupees } from "../../lib/format";
import { meKey, useWritable } from "../../lib/session";

type Member = Schemas["MemberOut"];
type Role = Schemas["RoleOut"];
type Team = Schemas["TeamOut"];

export function usePeopleData() {
  const people = useQuery({ queryKey: ["people"], queryFn: async () => unwrap(await api.GET("/api/v1/people")) });
  const roles = useQuery({ queryKey: ["roles"], queryFn: async () => unwrap(await api.GET("/api/v1/roles")) });
  const teams = useQuery({ queryKey: ["teams"], queryFn: async () => unwrap(await api.GET("/api/v1/teams")) });
  return { people, roles, teams };
}

function RoleOptions({ roles }: { roles: Role[] }) {
  return (
    <>
      <optgroup label="Full users">
        {roles.filter((r) => r.user_type === "full").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
      </optgroup>
      <optgroup label="Light users (shop floor)">
        {roles.filter((r) => r.user_type === "light").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
      </optgroup>
    </>
  );
}

export function PeoplePage() {
  const writable = useWritable();
  const queryClient = useQueryClient();
  const { people, roles, teams } = usePeopleData();
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const manage = Boolean(people.data?.can_manage) && writable;

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["people"] });
    queryClient.invalidateQueries({ queryKey: ["teams"] });
    queryClient.invalidateQueries({ queryKey: meKey });
  };
  const resend = useMutation({
    mutationFn: async (id: number) =>
      unwrap(await api.POST("/api/v1/people/invitations/{invitation_id}/resend", { params: { path: { invitation_id: id } } })),
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: async (id: number) =>
      unwrap(await api.DELETE("/api/v1/people/invitations/{invitation_id}", { params: { path: { invitation_id: id } } })),
    onSuccess: refresh,
  });

  if (people.isLoading || roles.isLoading || teams.isLoading) return <Loading />;
  if (people.error || !people.data) return <ErrorText error={people.error} />;
  const { seats } = people.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="People"
        description="Everyone in your company, each with a role. Full users are office staff; light users record one or two things on the shop floor."
        actions={manage ? <button type="button" className="btn-primary" onClick={() => setInviting(true)}>Invite someone</button> : null}
      />

      <Card>
        <div className="grid gap-6 sm:grid-cols-2">
          <Meter label="Users" used={seats.full + seats.full_invited} limit={seats.full_included}
            note="Your plan's users, shared across the whole company. Open invitations hold a place." />
          <Meter label="Light users" used={seats.light + seats.light_invited} limit={seats.light_included}
            note={seats.extra_light > 0
              ? `${seats.extra_light} more than your plan includes: ${rupees(seats.extra_light * seats.extra_light_monthly)}/month from your next renewal.`
              : `More than this costs ${rupees(seats.extra_light_monthly)}/month each.`} />
        </div>
      </Card>

      <Card title="Members" className="overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="border-b border-gray-100">
            <tr>
              <th scope="col" className="table-head">Name</th>
              <th scope="col" className="table-head">Role</th>
              <th scope="col" className="table-head">Teams</th>
              <th scope="col" className="table-head">Status</th>
              <th scope="col" className="table-head"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {people.data.members.map((m) => (
              <tr key={m.id} className={m.is_active ? "" : "opacity-60"}>
                <td className="table-cell">
                  <p className="font-semibold text-navy-900">{m.name} {m.is_me ? <Badge tone="navy">You</Badge> : null}</p>
                  <p className="text-xs text-gray-500">{m.email}</p>
                </td>
                <td className="table-cell">
                  {m.role.name}
                  {m.role.user_type === "light" ? <span className="ml-1 text-xs text-gray-500">(light)</span> : null}
                </td>
                <td className="table-cell">{m.teams.map((t) => t.name).join(", ") || <span className="text-gray-400">—</span>}</td>
                <td className="table-cell">{m.is_active ? <Badge tone="green">Active</Badge> : <Badge>Switched off</Badge>}</td>
                <td className="table-cell text-right">
                  {manage ? <button type="button" className="btn-link" onClick={() => setEditing(m)}>Change</button> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {people.data.can_manage ? (
        <Card title="Invitations" description="Waiting to be accepted. Links work for 7 days.">
          <ErrorText error={resend.error ?? revoke.error} />
          {people.data.invitations.length === 0 ? (
            <Empty title="No open invitations" />
          ) : (
            <ul className="divide-y divide-gray-100">
              {people.data.invitations.map((i) => (
                <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                  <div>
                    <p className="font-semibold text-navy-900">{i.name} <span className="font-normal text-gray-500">{i.email}</span></p>
                    <p className="text-xs text-gray-500">
                      {i.role.name} · invited by {i.invited_by} · {i.expired ? <span className="text-red-700">expired {day(i.expires_at)}</span> : `expires ${day(i.expires_at)}`}
                    </p>
                  </div>
                  {manage ? (
                    <div className="flex gap-3">
                      <button type="button" className="btn-link" disabled={resend.isPending} onClick={() => resend.mutate(i.id)}>Resend</button>
                      <button type="button" className="btn-link text-red-700" disabled={revoke.isPending} onClick={() => revoke.mutate(i.id)}>Revoke</button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {inviting ? <InviteForm roles={roles.data ?? []} teams={teams.data ?? []} onClose={() => setInviting(false)} onDone={refresh} /> : null}
      {editing ? <MemberForm member={editing} roles={roles.data ?? []} teams={teams.data ?? []} onClose={() => setEditing(null)} onDone={refresh} /> : null}
    </div>
  );
}

function TeamChecks({ teams, selected, onChange }: { teams: Team[]; selected: number[]; onChange: (ids: number[]) => void }) {
  if (teams.length === 0) return <p className="text-sm text-gray-500">No teams yet. You can add them under Teams.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {teams.map((t) => {
        const on = selected.includes(t.id);
        return (
          <label key={t.id} className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${on ? "border-accent-500 bg-accent-50 font-semibold" : "border-gray-200"}`}>
            <input type="checkbox" className="sr-only" checked={on} onChange={() => onChange(on ? selected.filter((id) => id !== t.id) : [...selected, t.id])} />
            {t.name}
          </label>
        );
      })}
    </div>
  );
}

function InviteForm({ roles, teams, onClose, onDone }: { roles: Role[]; teams: Team[]; onClose: () => void; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [roleId, setRoleId] = useState<number>(roles.find((r) => r.key === "purchase_executive")?.id ?? roles[0]?.id ?? 0);
  const [teamIds, setTeamIds] = useState<number[]>([]);
  const invite = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/people/invitations", {
        body: { email, first_name: firstName, last_name: lastName, role_id: roleId, team_ids: teamIds },
      })),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  const role = roles.find((r) => r.id === roleId);
  return (
    <Modal
      title="Invite someone"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!email || !firstName || invite.isPending} onClick={() => invite.mutate()}>
            {invite.isPending ? "Sending…" : "Send invitation"}
          </button>
        </>
      }
    >
      <Field label="Work email">{(id) => <input id={id} type="email" className="field-input" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="First name">{(id) => <input id={id} className="field-input" value={firstName} onChange={(e) => setFirstName(e.target.value)} />}</Field>
        <Field label="Last name (optional)">{(id) => <input id={id} className="field-input" value={lastName} onChange={(e) => setLastName(e.target.value)} />}</Field>
      </div>
      <Field label="Role" hint={role ? `${role.description}${role.user_type === "light" ? " · a light user" : " · a full user"}` : undefined}>
        {(id) => (
          <select id={id} className="field-input" value={roleId} onChange={(e) => setRoleId(Number(e.target.value))}>
            <RoleOptions roles={roles} />
          </select>
        )}
      </Field>
      <div>
        <p className="field-label">Teams (optional)</p>
        <TeamChecks teams={teams} selected={teamIds} onChange={setTeamIds} />
      </div>
      <ErrorText error={invite.error} />
    </Modal>
  );
}

function MemberForm({ member, roles, teams, onClose, onDone }: {
  member: Member; roles: Role[]; teams: Team[]; onClose: () => void; onDone: () => void;
}) {
  const [roleId, setRoleId] = useState(member.role.id);
  const [teamIds, setTeamIds] = useState(member.teams.map((t) => t.id));
  const update = useMutation({
    mutationFn: async (body: Schemas["MemberIn"]) =>
      unwrap(await api.PATCH("/api/v1/people/members/{member_id}", { params: { path: { member_id: member.id } }, body })),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal
      title={member.name}
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className={member.is_active ? "btn-danger mr-auto" : "btn-secondary mr-auto"}
            disabled={update.isPending}
            onClick={() => update.mutate({ is_active: !member.is_active })}
          >
            {member.is_active ? "Switch off access" : "Turn access back on"}
          </button>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={update.isPending} onClick={() => update.mutate({ role_id: roleId, team_ids: teamIds })}>
            Save
          </button>
        </>
      }
    >
      <p className="text-sm text-gray-500">{member.email}</p>
      <Field label="Role">
        {(id) => (
          <select id={id} className="field-input" value={roleId} onChange={(e) => setRoleId(Number(e.target.value))}>
            <RoleOptions roles={roles} />
          </select>
        )}
      </Field>
      <div>
        <p className="field-label">Teams</p>
        <TeamChecks teams={teams} selected={teamIds} onChange={setTeamIds} />
      </div>
      <p className="text-xs text-gray-500">Switching off access keeps everything they did; they just can't sign in to this company.</p>
      <ErrorText error={update.error} />
    </Modal>
  );
}
