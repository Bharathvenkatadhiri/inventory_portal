import { api, unwrap } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { ErrorText, Loading, Notice, PageHeader } from "../../components/ui";
import { PdfButton } from "../../components/PdfButton";
import { QuoteList, QuotationView } from "../sales/QuotationsPage";
import { ReasonModal, StatusBadge } from "./PurchaseLayout";

/** Purchase > Quotations received: offers suppliers sent us on MakeSetu. */
export function QuotationsReceivedPage() {
  const list = useQuery({ queryKey: ["quotations-received"], queryFn: async () => unwrap(await api.GET("/api/v1/purchase/quotations-received")) });
  return (
    <div className="space-y-6">
      <PageHeader title="Quotations received" description="Quotations your suppliers sent you on MakeSetu. Accept or decline each one; once you accept, the supplier raises the order." />
      <QuoteList rows={list.data} loading={list.isLoading} error={list.error} partyLabel="Supplier" to="/purchase/quotations-received/$quotationId" />
    </div>
  );
}

export function QuotationReceivedPage() {
  const { quotationId } = useParams({ strict: false }) as { quotationId: string };
  const queryClient = useQueryClient();
  const key = ["quotations-received", quotationId];
  const quote = useQuery({
    queryKey: key,
    queryFn: async () => unwrap(await api.GET("/api/v1/purchase/quotations-received/{quotation_id}", { params: { path: { quotation_id: Number(quotationId) } } })),
  });
  const [declining, setDeclining] = useState(false);
  const respond = async (accepted: boolean, note: string) => {
    const q = unwrap(await api.POST("/api/v1/purchase/quotations-received/{quotation_id}/response", {
      params: { path: { quotation_id: Number(quotationId) } }, body: { accepted, note },
    }));
    queryClient.setQueryData(key, q);
    queryClient.invalidateQueries({ queryKey: ["quotations-received"], exact: true });
  };
  const accept = useMutation({ mutationFn: () => respond(true, "") });
  if (quote.isLoading) return <Loading />;
  if (quote.error || !quote.data) return <ErrorText error={quote.error} />;
  const q = quote.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title={`${q.number} from ${q.seller.name}`}
        description={<span className="flex items-center gap-2"><StatusBadge status={q.status === "expired" ? "declined" : q.status} label={q.status_label} /><Link to="/purchase/quotations-received" className="btn-link">All quotations</Link></span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <PdfButton url={`/api/v1/quotations/${q.id}/pdf`} />
            {q.actions.respond ? <button type="button" className="btn-secondary" onClick={() => setDeclining(true)}>Decline</button> : null}
            {q.actions.respond ? <button type="button" className="btn-primary" disabled={accept.isPending} onClick={() => accept.mutate()}>Accept</button> : null}
          </div>
        }
      />
      {q.expired ? <Notice tone="amber">This quotation's validity ran out on its own date. Ask the supplier for a revised one.</Notice> : null}
      <ErrorText error={accept.error} />
      <QuotationView q={q} seller={false} />
      {declining ? (
        <ReasonModal title="Decline the quotation" label="Why? The supplier sees this (price, delivery…)." button="Decline"
          onClose={() => setDeclining(false)} send={async (reason) => { await respond(false, reason); setDeclining(false); }} />
      ) : null}
    </div>
  );
}
