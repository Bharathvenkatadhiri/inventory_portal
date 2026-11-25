import { api, unwrap } from "@makesetu/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, ErrorText, Field, Notice, PageHeader } from "../components/ui";
import { meKey, useMe } from "../lib/session";

export function ProfilePage() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const [firstName, setFirstName] = useState(me?.user.first_name ?? "");
  const [lastName, setLastName] = useState(me?.user.last_name ?? "");
  const [phone, setPhone] = useState(me?.user.phone ?? "");
  const save = useMutation({
    mutationFn: async () => unwrap(await api.PATCH("/api/v1/me", { body: { first_name: firstName, last_name: lastName, phone } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meKey }),
  });
  if (!me) return null;
  return (
    <div className="max-w-xl space-y-6">
      <PageHeader title="My profile" description="Your own details, the same in every company you work in." />
      <Card>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="First name">{(id) => <input id={id} className="field-input" value={firstName} onChange={(e) => setFirstName(e.target.value)} />}</Field>
            <Field label="Last name">{(id) => <input id={id} className="field-input" value={lastName} onChange={(e) => setLastName(e.target.value)} />}</Field>
          </div>
          <Field label="Email" hint="You sign in with this. It can't be changed here.">
            {(id) => <input id={id} className="field-input" value={me.user.email} disabled />}
          </Field>
          <Field label="Mobile number">{(id) => <input id={id} type="tel" className="field-input" value={phone} onChange={(e) => setPhone(e.target.value)} />}</Field>
          {save.isSuccess ? <Notice tone="green">Saved.</Notice> : null}
          <ErrorText error={save.error} />
          <div className="flex items-center justify-between">
            <a href="/password-change/" className="btn-link">Change password</a>
            <button type="submit" className="btn-primary" disabled={save.isPending}>Save</button>
          </div>
        </form>
      </Card>
    </div>
  );
}
