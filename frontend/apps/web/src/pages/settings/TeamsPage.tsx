import { api, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Card, Empty, ErrorText, Field, Loading, Meter, Modal, PageHeader } from "../../components/ui";
import { meKey, useActive, useCan, useWritable } from "../../lib/session";
import { usePeopleData } from "./PeoplePage";

type Team = Schemas["TeamOut"];
type Member = Schemas["MemberOut"];

export function TeamsPage() {
  const can = useCan();
  const writable = useWritable();
  const { seats } = useActive();
  const manage = can("users.manage") && writable;
  const { people, teams } = usePeopleData();
  const [editing, setEditing] = useState<Team | "new" | null>(null);
  if (teams.isLoading || people.isLoading) return <Loading />;
  if (teams.error) return <ErrorText error={teams.error} />;
  const atLimit = seats.teams_included !== null && seats.teams_included !== undefined && seats.teams >= seats.teams_included;
  const canBill = can("billing.manage");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Teams"
        description="Departments such as Purchase, Store or QC. A team's approvals go to its head, alerts go to the team, and reports group by team. People can be in several teams and still count once."
        actions={manage ? <button type="button" className="btn-primary" disabled={atLimit} onClick={() => setEditing("new")}>Create a team</button> : null}
      />
      <Card>
        <Meter
          label="Teams"
          used={seats.teams}
          limit={seats.teams_included}
          note={atLimit ? (
            <>Your plan's teams are all in use. {canBill ? <Link to="/settings/billing" className="font-semibold text-accent-600 underline">Move to a bigger plan</Link> : "Ask your Org Admin about a bigger plan."}</>
          ) : undefined}
        />
      </Card>
      {(teams.data ?? []).length === 0 ? (
        <Empty title="No teams yet">{manage ? "Create one for each department that approves or gets alerts." : null}</Empty>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {(teams.data ?? []).map((t) => (
            <li key={t.id} className="card">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-navy-900">{t.name}</p>
                  <p className="text-sm text-gray-500">Head: {t.head?.name ?? "none"}</p>
                </div>
                {manage ? <button type="button" className="btn-link" onClick={() => setEditing(t)}>Edit</button> : null}
              </div>
              <p className="mt-3 text-sm text-gray-600">{t.members.map((m) => m.name).join(", ") || "No one in it yet."}</p>
            </li>
          ))}
        </ul>
      )}
      {editing ? (
        <TeamForm team={editing === "new" ? null : editing} members={(people.data?.members ?? []).filter((m) => m.is_active)}
          onClose={() => setEditing(null)} />
      ) : null}
    </div>
  );
}

function TeamForm({ team, members, onClose }: { team: Team | null; members: Member[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(team?.name ?? "");
  const [headId, setHeadId] = useState<number | null>(team?.head?.id ?? null);
  const [memberIds, setMemberIds] = useState<number[]>(team?.members.map((m) => m.id) ?? []);
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["teams"] });
    queryClient.invalidateQueries({ queryKey: ["people"] });
    queryClient.invalidateQueries({ queryKey: meKey });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () => {
      const body = { name, head_id: headId, member_ids: memberIds };
      return team
        ? unwrap(await api.PUT("/api/v1/teams/{team_id}", { params: { path: { team_id: team.id } }, body }))
        : unwrap(await api.POST("/api/v1/teams", { body }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/teams/{team_id}", { params: { path: { team_id: team!.id } } })),
    onSuccess: done,
  });
  return (
    <Modal
      title={team ? `Edit ${team.name}` : "Create a team"}
      onClose={onClose}
      footer={
        <>
          {team ? <button type="button" className="btn-danger mr-auto" onClick={() => remove.mutate()}>Delete team</button> : null}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
        </>
      }
    >
      <Field label="Name">{(id) => <input id={id} className="field-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Purchase" />}</Field>
      <Field label="Head" hint="Approvals from this team go to its head.">
        {(id) => (
          <select id={id} className="field-input" value={headId ?? ""} onChange={(e) => setHeadId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">No head</option>
            {members.map((m) => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
          </select>
        )}
      </Field>
      <fieldset>
        <legend className="field-label">Members</legend>
        <div className="max-h-60 space-y-1 overflow-y-auto rounded-lg border border-gray-200 p-2">
          {members.map((m) => {
            const on = memberIds.includes(m.user_id) || headId === m.user_id;
            return (
              <label key={m.user_id} className="flex items-center gap-2 rounded px-1 py-1 text-sm">
                <input
                  type="checkbox"
                  className="rounded border-gray-300 text-accent-500"
                  checked={on}
                  disabled={headId === m.user_id}
                  onChange={() => setMemberIds(on ? memberIds.filter((id) => id !== m.user_id) : [...memberIds, m.user_id])}
                />
                {m.name} <span className="text-xs text-gray-500">{m.role.name}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <ErrorText error={save.error ?? remove.error} />
    </Modal>
  );
}
