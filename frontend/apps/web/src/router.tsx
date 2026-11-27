import { createRootRoute, createRoute, createRouter, Link, redirect } from "@tanstack/react-router";

import { AppShell } from "./components/AppShell";
import { AccountsLayout } from "./pages/accounts/AccountsLayout";
import { PayablesPage, ReceivablesPage } from "./pages/accounts/OutstandingPage";
import { GstReturnsPage } from "./pages/accounts/GstReturnsPage";
import { PaymentsPage } from "./pages/accounts/PaymentsPage";
import { TdsPage } from "./pages/accounts/TdsPage";
import { HomePage } from "./pages/HomePage";
import { QcPage } from "./pages/quality/QcPage";
import { BomsPage } from "./pages/production/BomsPage";
import { CostingPage } from "./pages/production/CostingPage";
import { JobWorkPage } from "./pages/production/JobWorkPage";
import { WorkOrdersPage } from "./pages/production/WorkOrdersPage";
import { ProductionIndex, ProductionLayout } from "./pages/production/ProductionLayout";
import { ProductionPage } from "./pages/production/ProductionPage";
import { SalesNotesPage, SupplierNotesPage } from "./pages/notes/NotesPage";
import { InventoryLayout } from "./pages/inventory/InventoryLayout";
import { BatchesPage } from "./pages/inventory/BatchesPage";
import { ItemsPage } from "./pages/inventory/ItemsPage";
import { MsmePage } from "./pages/accounts/MsmePage";
import { GstPortalPage } from "./components/GstPortal";
import { QuotationReceivedPage, QuotationsReceivedPage } from "./pages/purchase/QuotationsReceivedPage";
import { RfqDetailPage, RfqEditor, RfqsPage } from "./pages/purchase/RfqsPage";
import { QuotationDetailPage, QuotationEditor, QuotationsPage } from "./pages/sales/QuotationsPage";
import { RfqsReceivedPage } from "./pages/sales/RfqsReceivedPage";
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
const batchesRoute = createRoute({ getParentRoute: () => inventoryRoute, path: "batches", component: BatchesPage });
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
const rfqsRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "rfqs", component: RfqsPage });
const newRfqRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "rfqs/new", component: RfqEditor });
const rfqRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "rfqs/$rfqId", component: RfqDetailPage });
const editRfqRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "rfqs/$rfqId/edit", component: RfqEditor });
const quotationsReceivedRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "quotations-received", component: QuotationsReceivedPage });
const quotationReceivedRoute = createRoute({ getParentRoute: () => purchaseRoute, path: "quotations-received/$quotationId", component: QuotationReceivedPage });
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
const quotationsRoute = createRoute({ getParentRoute: () => salesRoute, path: "quotations", component: QuotationsPage });
const newQuotationRoute = createRoute({ getParentRoute: () => salesRoute, path: "quotations/new", component: QuotationEditor });
const quotationRoute = createRoute({ getParentRoute: () => salesRoute, path: "quotations/$quotationId", component: QuotationDetailPage });
const editQuotationRoute = createRoute({ getParentRoute: () => salesRoute, path: "quotations/$quotationId/edit", component: QuotationEditor });
const rfqsReceivedRoute = createRoute({ getParentRoute: () => salesRoute, path: "rfqs-received", component: RfqsReceivedPage });
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
const tdsRoute = createRoute({ getParentRoute: () => accountsRoute, path: "tds", component: TdsPage });
const msmeRoute = createRoute({ getParentRoute: () => accountsRoute, path: "msme", component: MsmePage });
const gstPortalRoute = createRoute({ getParentRoute: () => accountsRoute, path: "gst-portal", component: GstPortalPage });
const gstReturnsRoute = createRoute({ getParentRoute: () => accountsRoute, path: "gst-returns", component: GstReturnsPage });
const productionRoute = createRoute({ getParentRoute: () => rootRoute, path: "production", component: ProductionLayout });
const productionIndexRoute = createRoute({ getParentRoute: () => productionRoute, path: "/", component: ProductionIndex });
const productionEntriesRoute = createRoute({ getParentRoute: () => productionRoute, path: "entries", component: ProductionPage });
const bomsRoute = createRoute({ getParentRoute: () => productionRoute, path: "boms", component: BomsPage });
const workOrdersRoute = createRoute({ getParentRoute: () => productionRoute, path: "work-orders", component: WorkOrdersPage });
const jobWorkRoute = createRoute({ getParentRoute: () => productionRoute, path: "job-work", component: JobWorkPage });
const costingRoute = createRoute({ getParentRoute: () => productionRoute, path: "costing", component: CostingPage });
const qcRoute = createRoute({ getParentRoute: () => rootRoute, path: "qc", component: QcPage });
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
  qcRoute,
  productionRoute.addChildren([productionIndexRoute, workOrdersRoute, productionEntriesRoute, bomsRoute, jobWorkRoute, costingRoute]),
  accountsRoute.addChildren([accountsIndexRoute, receivablesRoute, payablesRoute, paymentsRoute, tdsRoute, gstReturnsRoute, msmeRoute, gstPortalRoute]),
  purchaseRoute.addChildren([purchaseIndexRoute, ordersRoute, newOrderRoute, orderRoute, editOrderRoute, receiptsRoute, billsRoute, supplierNotesRoute, rfqsRoute, newRfqRoute, rfqRoute, editRfqRoute, quotationsReceivedRoute, quotationReceivedRoute]),
  salesRoute.addChildren([salesIndexRoute, ordersReceivedRoute, salesOrdersRoute, newSalesOrderRoute, salesOrderRoute, editSalesOrderRoute, dispatchesRoute, invoicesRoute, salesNotesRoute, quotationsRoute, newQuotationRoute, quotationRoute, editQuotationRoute, rfqsReceivedRoute]),
  inventoryRoute.addChildren([inventoryIndexRoute, itemsRoute, warehousesRoute, stockRoute, batchesRoute]),
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
