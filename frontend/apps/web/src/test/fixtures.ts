import type { Schemas } from "@makesetu/api";

export function active(overrides: Partial<Schemas["SubscriptionOut"]> = {}, allowed: string[] = []): Schemas["ActiveOut"] {
  return {
    company: {
      id: 1, name: "Vaigai", legal_name: "Vaigai Precision Works Private Limited", trade_name: "", gstin: "33AAAAA0000A1Z5",
      pan: "AAAAA0000A", gst_status: "Active", principal_address: "", city: "Madurai", state: "Tamil Nadu", state_code: "33",
      pincode: "625001", buying_enabled: true, selling_enabled: true, findable: true, plan: "starter", plan_label: "Starter",
      tan: "", tds_194q: false,
    },
    role: { id: 1, key: "org_admin", name: "Org Admin", user_type: "full" },
    granted: allowed,
    allowed,
    subscription: {
      plan: "starter", plan_label: "Starter", status: "trialing", entitled: true, trial_ends_at: "2026-12-08T10:00:00Z",
      current_period_start: null, expires_at: null, grace_until: null, auto_renew: false, scheduled_plan: null, days_left: 13,
      price: "0.00", next_amount: "1500.00", next_plan_amount: "1500.00", next_extra_light_users: 0, ...overrides,
    },
    seats: {
      full: 1, full_invited: 0, full_included: 3, light: 0, light_invited: 0, light_included: 5, extra_light: 0,
      extra_light_monthly: 150, teams: 0, teams_included: 2,
    },
  };
}

export const permissions: Schemas["PermissionOut"][] = [
  { code: "po.create", label: "Create purchase orders", module: "Purchase", side: "buying", light_allowed: false, included: true, needs_plan: null, side_enabled: true },
  { code: "grn.post", label: "Post goods receipts (GRN)", module: "Store", side: "buying", light_allowed: true, included: true, needs_plan: null, side_enabled: true },
  { code: "work_order.manage", label: "Create and update work orders", module: "Production", side: null, light_allowed: false, included: false, needs_plan: "Growth", side_enabled: true },
];
