import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function IngestionPage() {
  return (
    <>
      <PageHeader
        title="Ingestion"
        description="Track ingestion jobs and trigger a knowledge-base refresh (admin only)."
      />
      <EmptyState title="Not built yet">Job status and the trigger control are wired to the ingestion API in Phases 5 and 6.</EmptyState>
    </>
  );
}
