import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function TicketAnalysisPage() {
  return (
    <>
      <PageHeader
        title="Ticket Analysis"
        description="Submit a support ticket and receive an AI-generated draft resolution with classification, priority, cited sources, and grounding checks."
      />
      <EmptyState title="Not built yet">The analysis form is built on the real POST /tickets/analyze endpoint (Phase 4 backend, Phase 6 UI).</EmptyState>
    </>
  );
}
