import { createRootRoute, createRoute, createRouter, Link, redirect } from "@tanstack/react-router";

import { AppShell } from "./components/AppShell";
import { HomePage } from "./pages/HomePage";
import { PartnersPage } from "./pages/PartnersPage";
import { ProfilePage } from "./pages/ProfilePage";
import { BillingPage } from "./pages/settings/BillingPage";
import { CompanyPage } from "./pages/settings/CompanyPage";
import { PeoplePage } from "./pages/settings/PeoplePage";
import { RolesPage } from "./pages/settings/RolesPage";
import { SettingsLayout } from "./pages/settings/SettingsLayout";
import { TeamsPage } from "./pages/settings/TeamsPage";

function NotFound() {
  return (
    <div className="py-16 text-center">
      <p className="text-lg font-bold">That page doesn't exist</p>
      <Link to="/" className="btn-link mt-2">Go to Home</Link>
    </div>
  );
}

const rootRoute = createRootRoute({ component: AppShell, notFoundComponent: NotFound });
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: HomePage });
const partnersRoute = createRoute({ getParentRoute: () => rootRoute, path: "partners", component: PartnersPage });
const profileRoute = createRoute({ getParentRoute: () => rootRoute, path: "profile", component: ProfilePage });
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "settings", component: SettingsLayout });
const settingsIndexRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/settings/company" });
  },
});
const companyRoute = createRoute({ getParentRoute: () => settingsRoute, path: "company", component: CompanyPage });
const peopleRoute = createRoute({ getParentRoute: () => settingsRoute, path: "people", component: PeoplePage });
const rolesRoute = createRoute({ getParentRoute: () => settingsRoute, path: "roles", component: RolesPage });
const teamsRoute = createRoute({ getParentRoute: () => settingsRoute, path: "teams", component: TeamsPage });
const billingRoute = createRoute({ getParentRoute: () => settingsRoute, path: "billing", component: BillingPage });

const routeTree = rootRoute.addChildren([
  homeRoute,
  partnersRoute,
  profileRoute,
  settingsRoute.addChildren([settingsIndexRoute, companyRoute, peopleRoute, rolesRoute, teamsRoute, billingRoute]),
]);

// Every app path is under /app (Django serves the page there, organizations.views.app_shell).
export const router = createRouter({ routeTree, basepath: "/app" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
