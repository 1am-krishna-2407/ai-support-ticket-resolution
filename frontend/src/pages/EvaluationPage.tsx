import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function EvaluationPage() {
  return (
    <>
      <PageHeader
        title="Evaluation"
        description="Retrieval and answer-quality metrics from stored evaluation runs, with run comparison and failed-case inspection."
      />
      <EmptyState title="Not built yet">No evaluation results available yet. Runs are produced by the evaluation runner (Phase 7).</EmptyState>
    </>
  );
}
