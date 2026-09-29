import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function RetrievalExplorerPage() {
  return (
    <>
      <PageHeader
        title="Retrieval Explorer"
        description="Run vector, lexical, or hybrid searches against the knowledge base and inspect every score."
      />
      <EmptyState title="Not built yet">Available once the retrieval API exists (Phase 3 backend, Phase 6 UI).</EmptyState>
    </>
  );
}
