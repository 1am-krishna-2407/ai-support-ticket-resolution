import { Outlet, type RouteObject } from "react-router";

import { AuthProvider } from "./auth/AuthContext";
import { RequireAdmin, RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { AccountPage } from "./pages/AccountPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { HistoryDetailPage } from "./pages/HistoryDetailPage";
import { HistoryPage } from "./pages/HistoryPage";
import { IngestionPage } from "./pages/IngestionPage";
import { KnowledgeBasePage } from "./pages/KnowledgeBasePage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RegisterPage } from "./pages/RegisterPage";
import { RetrievalExplorerPage } from "./pages/RetrievalExplorerPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { TicketAnalysisPage } from "./pages/TicketAnalysisPage";
import { UsersPage } from "./pages/UsersPage";

export const routes: RouteObject[] = [
  {
    // Everything shares one auth state; only /login is reachable signed out.
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        errorElement: <RouteErrorBoundary />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "tickets", element: <TicketAnalysisPage /> },
          { path: "history", element: <HistoryPage /> },
          { path: "history/:analysisId", element: <HistoryDetailPage /> },
          { path: "retrieval", element: <RetrievalExplorerPage /> },
          { path: "knowledge-base", element: <KnowledgeBasePage /> },
          { path: "knowledge-base/:documentId", element: <DocumentDetailPage /> },
          { path: "ingestion", element: <IngestionPage /> },
          { path: "evaluation", element: <EvaluationPage /> },
          { path: "status", element: <SystemStatusPage /> },
          {
            path: "users",
            element: (
              <RequireAdmin>
                <UsersPage />
              </RequireAdmin>
            ),
          },
          { path: "account", element: <AccountPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
