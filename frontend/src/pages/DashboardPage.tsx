import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Knowledge-base size, ticket analysis activity, grounding and citation rates, and the latest evaluation results."
      />
      <EmptyState title="Not built yet">Metrics appear once the backend summary endpoint and evaluation runs exist (Phases 5 and 7). Until then this page shows nothing rather than invented numbers.</EmptyState>
    </>
  );
}
