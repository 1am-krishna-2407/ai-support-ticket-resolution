import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function SystemStatusPage() {
  return (
    <>
      <PageHeader
        title="System Status"
        description="Health of the API, database, vector search, embedding provider, LLM provider, and knowledge base."
      />
      <EmptyState title="Not built yet">Per-dependency readiness comes from GET /ready (Phase 5). The API liveness indicator in the header is already live.</EmptyState>
    </>
  );
}
