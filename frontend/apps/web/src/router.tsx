import { createRootRoute, createRoute, createRouter, Link, redirect } from "@tanstack/react-router";

import { AppShell } from "./components/AppShell";
import { AccountsLayout } from "./pages/accounts/AccountsLayout";
import { PayablesPage, ReceivablesPage } from "./pages/accounts/OutstandingPage";
import { PaymentsPage } from "./pages/accounts/PaymentsPage";
import { HomePage } from "./pages/HomePage";
import { SalesNotesPage, SupplierNotesPage } from "./pages/notes/NotesPage";
import { InventoryLayout } from "./pages/inventory/InventoryLayout";
import { ItemsPage } from "./pages/inventory/ItemsPage";
import { StockPage } from "./pages/inventory/StockPage";
import { WarehousesPage } from "./pages/inventory/WarehousesPage";
import { PartnersPage } from "./pages/PartnersPage";
import { ProfilePage } from "./pages/ProfilePage";
import { BillsPage } from "./pages/purchase/BillsPage";
import { OrderDetailPage } from "./pages/purchase/OrderDetailPage";
import { OrderEditor } from "./pages/purchase/OrderEditor";
import { OrdersPage } from "./pages/purchase/OrdersPage";
import { PurchaseLayout } from "./pages/purchase/PurchaseLayout";
import { ReceiptsPage } from "./pages/purchase/ReceiptsPage";
import { DispatchesPage } from "./pages/sales/DispatchesPage";
import { InvoicesPage } from "./pages/sales/InvoicesPage";
import { OrdersReceivedPage } from "./pages/sales/OrdersReceivedPage";
import { SalesLayout } from "./pages/sales/SalesLayout";
import { SalesOrderDetailPage } from "./pages/sales/SalesOrderDetailPage";
import { SalesOrderEditor } from "./pages/sales/SalesOrderEditor";
import { SalesOrdersPage } from "./pages/sales/SalesOrdersPage";
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
const inventoryRoute = createRoute({ getParentRoute: () => rootRoute, path: "inventory", component: InventoryLayout });
const inventoryIndexRoute = createRoute({
  getParentRoute: () => inventoryRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/inventory/items" });
  },
});
const itemsRoute = createRoute({ getParentRoute: () => inventoryRoute, path: "items", component: ItemsPage });
const warehousesRoute = createRoute({ getParentRoute: () => inventoryRoute, path: "warehouses", component: WarehousesPage });
const stockRoute = createRoute({ getParentRoute: () => inventoryRoute, path: "stock", component: StockPage });
const purchaseRoute = createRoute({ getParentRoute: () => rootRoute, path: "purchase", component: PurchaseLayout });
const purchaseIndexRoute = createRoute({
  getParentRoute: () => purchaseRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/purchase/orders" });
  },
});
const ordersRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "orders", component: OrdersPage });
const newOrderRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "orders/new", component: OrderEditor });
const orderRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "orders/$orderId", component: OrderDetailPage });
const editOrderRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "orders/$orderId/edit", component: OrderEditor });
const receiptsRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "receipts", component: ReceiptsPage });
const billsRoute = createRoute({
  getParentRoute: () => purchaseRoute,
  path: "bills",
  component: BillsPage,
  // ?bill=12 opens that bill.
  validateSearch: (search: Record<string, unknown>): { bill?: number } => (search.bill ? { bill: Number(search.bill) } : {}),
});
const salesRoute = createRoute({ getParentRoute: () => rootRoute, path: "sales", component: SalesLayout });
const salesIndexRoute = createRoute({
  getParentRoute: () => salesRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/sales/orders-received" });
  },
});
// ?note=12 opens that note.
const noteSearch = (search: Record<string, unknown>): { note?: number } => (search.note ? { note: Number(search.note) } : {});
const salesNotesRoute = createRoute({ getParentRoute: () => salesRoute, path: "notes", component: SalesNotesPage, validateSearch: noteSearch });
const supplierNotesRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "notes", component: SupplierNotesPage, validateSearch: noteSearch });
const ordersReceivedRoute = createRoute({ getParentRoute: () => salesRoute, path: "orders-received", component: OrdersReceivedPage });
const salesOrdersRoute = createRoute({ getParentRoute: () => salesRoute, path: "orders", component: SalesOrdersPage });
const newSalesOrderRoute = createRoute({ getParentRoute: () => salesRoute, path: "orders/new", component: SalesOrderEditor });
const salesOrderRoute = createRoute({ getParentRoute: () => salesRoute, path: "orders/$orderId", component: SalesOrderDetailPage });
const editSalesOrderRoute = createRoute({ getParentRoute: () => salesRoute, path: "orders/$orderId/edit", component: SalesOrderEditor });
const dispatchesRoute = createRoute({ getParentRoute: () => salesRoute, path: "dispatches", component: DispatchesPage });
const invoicesRoute = createRoute({
  getParentRoute: () => salesRoute,
  path: "invoices",
  component: InvoicesPage,
  // ?invoice=12 opens that invoice.
  validateSearch: (search: Record<string, unknown>): { invoice?: number } => (search.invoice ? { invoice: Number(search.invoice) } : {}),
});
const accountsRoute = createRoute({ getParentRoute: () => rootRoute, path: "accounts", component: AccountsLayout });
const accountsIndexRoute = createRoute({
  getParentRoute: () => accountsRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/accounts/payments" });
  },
});
const receivablesRoute = createRoute({ getParentRoute: () => accountsRoute, path: "receivables", component: ReceivablesPage });
const payablesRoute = createRoute({ getParentRoute: () => accountsRoute, path: "payables", component: PayablesPage });
const paymentsRoute = createRoute({ getParentRoute: () => accountsRoute, path: "payments", component: PaymentsPage });
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
  accountsRoute.addChildren([accountsIndexRoute, receivablesRoute, payablesRoute, paymentsRoute]),
  purchaseRoute.addChildren([purchaseIndexRoute, ordersRoute, newOrderRoute, orderRoute, editOrderRoute, receiptsRoute, billsRoute, supplierNotesRoute]),
  salesRoute.addChildren([salesIndexRoute, ordersReceivedRoute, salesOrdersRoute, newSalesOrderRoute, salesOrderRoute, editSalesOrderRoute, dispatchesRoute, invoicesRoute, salesNotesRoute]),
  inventoryRoute.addChildren([inventoryIndexRoute, itemsRoute, warehousesRoute, stockRoute]),
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
