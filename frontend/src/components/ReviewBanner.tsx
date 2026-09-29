const REASON_LABELS: Record<string, string> = {
  insufficient_evidence: "Documentation does not cover this ticket well enough",
  retrieval_unavailable: "Documentation search was unavailable",
  retrieval_degraded: "Search ran without embeddings (exact-phrase matches only)",
  grounding_failed: "Draft failed grounding checks",
  citation_invalid: "Draft cited sources that were not provided",
  unsupported_identifiers: "Draft mentions commands or settings not found in its sources",
  invalid_model_output: "Model output was invalid",
  llm_unavailable: "Language model was unavailable",
  classification_unavailable: "Ticket could not be classified",
  low_classification_confidence: "Low classification confidence",
  ambiguous_category: "Category is ambiguous",
  high_or_critical_priority: "High or critical priority",
  security_concern: "Possible security concern",
  data_loss_risk: "Possible data loss",
  needs_more_information: "More information is needed from the customer",
  prompt_injection_suspected: "Ticket or sources contain instruction-like text (possible prompt injection)",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

/** Always-visible label: output is an AI draft, and why human review is required. */
export function ReviewBanner({ label, required, reasons }: { label: string; required: boolean; reasons: string[] }) {
  return (
    <section
      aria-label="Review status"
      className={`rounded-lg border px-4 py-3 text-sm ${
        required ? "border-amber-300 bg-amber-50" : "border-blue-200 bg-blue-50"
      }`}
    >
      <p className="font-semibold text-slate-900">{label}</p>
      {required ? (
        <>
          <p className="mt-1 text-amber-900">Human review required before anything is sent to a customer:</p>
          <ul className="mt-1 list-disc pl-5 text-amber-900">
            {reasons.map((r) => (
              <li key={r}>{reasonLabel(r)}</li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-1 text-blue-900">
          No automatic review triggers fired. A support engineer must still check the draft before use.
        </p>
      )}
    </section>
  );
}
