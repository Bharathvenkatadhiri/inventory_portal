import { announceSession, api, tab, unwrap, type Schemas } from "@makesetu/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type Me = Schemas["MeOut"];
export type Active = Schemas["ActiveOut"];

export const meKey = ["me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => {
      const me = unwrap(await api.GET("/api/v1/me"));
      // From here on this tab's requests say who and which organization it's showing.
      tab.remember(me.user.id, me.active?.company.id);
      announceSession(me.user.id);
      return me;
    },
  });
}

/**
 * The organization the person is working in. The app only renders pages once
 * it's loaded (AppShell), so pages can rely on it.
 */
export function useActive(): Active {
  const { data } = useMe();
  if (!data?.active) throw new Error("useActive() used outside a loaded organization");
  return data.active;
}

/** What the person can do right now: their role allows it, and the plan and sides do. */
export function useCan() {
  const active = useActive();
  return (code: string) => active.allowed.includes(code);
}

/** Changes are off while the subscription has lapsed (the server refuses them too). */
export function useWritable(): boolean {
  return useActive().subscription.entitled;
}

export function useSwitchOrganization() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (companyId: number) =>
      unwrap(await api.POST("/api/v1/me/active-organization", { body: { company_id: companyId } })),
    onSuccess: (me) => {
      // Only this tab moves; other open tabs stay on the organization they show.
      tab.remember(me.user.id, me.active?.company.id);
      // Everything else belongs to the other organization: start afresh.
      queryClient.clear();
      queryClient.setQueryData(meKey, me);
    },
  });
}
