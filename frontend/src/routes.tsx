import type { RouteObject } from "react-router";

import { AppShell } from "./components/AppShell";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { DashboardPage } from "./pages/DashboardPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { IngestionPage } from "./pages/IngestionPage";
import { KnowledgeBasePage } from "./pages/KnowledgeBasePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RetrievalExplorerPage } from "./pages/RetrievalExplorerPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { TicketAnalysisPage } from "./pages/TicketAnalysisPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "tickets", element: <TicketAnalysisPage /> },
      { path: "retrieval", element: <RetrievalExplorerPage /> },
      { path: "knowledge-base", element: <KnowledgeBasePage /> },
      { path: "ingestion", element: <IngestionPage /> },
      { path: "evaluation", element: <EvaluationPage /> },
      { path: "status", element: <SystemStatusPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
