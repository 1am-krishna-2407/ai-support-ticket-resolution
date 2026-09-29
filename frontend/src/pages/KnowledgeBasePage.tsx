import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function KnowledgeBasePage() {
  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Browse ingested documents, their versions, and the chunks used for retrieval."
      />
      <EmptyState title="Not built yet">Documents appear after the first ingestion run (Phase 2), browsable via the documents API (Phase 5).</EmptyState>
    </>
  );
}
