import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Card, ErrorText, Field, Loading, Notice } from "../../components/ui";

const KINDS = [
  { kind: "logo", label: "Logo", hint: "Top left of invoices, challans and purchase orders." },
  { kind: "signature", label: "Signature", hint: "Above “Authorised signatory”. A scan on a white background works best." },
] as const;

/** Logo, signature, signatory and invoice terms printed on the company's documents. */
export function BrandingCard({ manage }: { manage: boolean }) {
  const queryClient = useQueryClient();
  const branding = useQuery({ queryKey: ["branding"], queryFn: async () => unwrap(await api.GET("/api/v1/organization/branding")) });
  const [signatory, setSignatory] = useState("");
  const [terms, setTerms] = useState("");
  useEffect(() => {
    if (branding.data) {
      setSignatory(branding.data.signatory_name);
      setTerms(branding.data.invoice_terms);
    }
  }, [branding.data]);
  const done = (data: NonNullable<typeof branding.data>) => queryClient.setQueryData(["branding"], data);
  const save = useMutation({
    mutationFn: async () => unwrap(await api.PUT("/api/v1/organization/branding", { body: { signatory_name: signatory, invoice_terms: terms } })),
    onSuccess: done,
  });
  const upload = useMutation({
    mutationFn: async ({ kind, file }: { kind: string; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      return unwrap(await api.POST("/api/v1/organization/branding/{kind}", {
        params: { path: { kind } },
        // Sent as multipart form data, not JSON.
        body: form as unknown as { file: string },
        bodySerializer: (body) => body as unknown as FormData,
      }));
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async (kind: string) => unwrap(await api.DELETE("/api/v1/organization/branding/{kind}", { params: { path: { kind } } })),
    onSuccess: done,
  });

  if (branding.isLoading) return <Card title="On your documents"><Loading /></Card>;
  const data = branding.data;
  return (
    <Card title="On your documents" description="Printed on tax invoices, delivery challans and purchase orders. PNG or JPEG, up to 1 MB.">
      <div className="grid gap-6 sm:grid-cols-2">
        {KINDS.map(({ kind, label, hint }) => {
          const url = kind === "logo" ? data?.logo_url : data?.signature_url;
          return (
            <div key={kind}>
              <p className="field-label">{label}</p>
              <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50">
                {url ? <img src={url} alt={label} className="max-h-20 max-w-full object-contain" /> : <span className="text-xs text-gray-400">None yet</span>}
              </div>
              <p className="mt-1 text-xs text-gray-500">{hint}</p>
              {manage ? (
                <div className="mt-2 flex items-center gap-3">
                  <label className="btn-link cursor-pointer">
                    {url ? "Replace" : "Upload"}
                    <input type="file" accept="image/png,image/jpeg" className="sr-only" onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) upload.mutate({ kind, file });
                      e.target.value = "";
                    }} />
                  </label>
                  {url ? <button type="button" className="text-sm text-red-700 hover:underline" onClick={() => remove.mutate(kind)}>Remove</button> : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      <ErrorText error={upload.error ?? remove.error} />
      <div className="mt-6 grid gap-4">
        <Field label="Authorised signatory" hint="Name printed under the signature.">
          {(id) => <input id={id} className="field-input max-w-sm" value={signatory} disabled={!manage} onChange={(e) => setSignatory(e.target.value)} maxLength={100} />}
        </Field>
        <Field label="Terms on invoices" hint="E.g. payment terms, interest on late payment, jurisdiction.">
          {(id) => <textarea id={id} className="field-input" rows={3} value={terms} disabled={!manage} onChange={(e) => setTerms(e.target.value)} maxLength={2000} />}
        </Field>
        {manage ? (
          <div className="flex items-center gap-3">
            <button type="button" className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
            {save.isSuccess ? <Notice tone="green">Saved.</Notice> : null}
          </div>
        ) : null}
        <ErrorText error={save.error} />
      </div>
    </Card>
  );
}
