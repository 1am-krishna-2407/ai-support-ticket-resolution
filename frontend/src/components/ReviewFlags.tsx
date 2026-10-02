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
  needs_more_information: "Includes follow-up questions for the customer",
  prompt_injection_suspected: "Ticket or sources contain instruction-like text (possible prompt injection)",
};

/**
 * Reasons worth interrupting the reader for. Routine ones (priority, follow-up questions,
 * insufficient evidence, stage failures) are already visible elsewhere on the page.
 */
const SERIOUS = new Set([
  "security_concern",
  "data_loss_risk",
  "prompt_injection_suspected",
  "grounding_failed",
  "citation_invalid",
  "unsupported_identifiers",
]);

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

/** A one-line flag row, shown only when a serious review reason fired. */
export function ReviewFlags({ reasons }: { reasons: string[] }) {
  const serious = reasons.filter((r) => SERIOUS.has(r));
  if (!serious.length) return null;
  return (
    <section aria-label="Review flags" className="flex flex-wrap items-center gap-2 text-sm">
      <span className="font-medium text-slate-700">Check before sending:</span>
      {serious.map((r) => (
        <span key={r} className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-0.5 text-xs text-amber-900">
          {reasonLabel(r)}
        </span>
      ))}
    </section>
  );
}
