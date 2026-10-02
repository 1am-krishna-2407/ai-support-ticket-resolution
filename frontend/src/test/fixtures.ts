import type { Me, Readiness, RetrievalResult, TicketAnalysis } from "../services/schemas";

export const chunk = (overrides: Partial<RetrievalResult["results"][number]> = {}) => ({
  source_id: "SRC-001",
  chunk_id: "c1",
  document_id: "runner-configuration-tls-self-signed",
  document_version: 1,
  title: "Self-signed certificates",
  section: "Trusting certificates",
  subsection: null,
  heading_path: "Self-signed certificates > Trusting certificates",
  url: "https://docs.gitlab.com/runner/configuration/tls-self-signed/#trusting",
  category: "runners",
  content: "Set tls-ca-file in config.toml.",
  token_count: 20,
  exact_phrase_match: false,
  passes_threshold: true,
  scores: { semantic: 0.81, lexical: 0.2, fused: 0.032, rerank: null, final: 0.81 },
  ranks: { semantic: 1, lexical: 2, phrase: null },
  ...overrides,
});

export const retrievalResult = (overrides: Partial<RetrievalResult> = {}): RetrievalResult => ({
  query: "x509",
  retrieval_query: "x509",
  extracted_terms: {},
  phrases: [],
  method: "hybrid",
  top_k: 8,
  threshold: 0.6,
  threshold_basis: "semantic_similarity",
  evidence_sufficient: true,
  reason: "1 result(s) meet the threshold 0.60 on semantic_similarity (best 0.810).",
  degraded: false,
  warnings: [],
  results: [chunk()],
  diagnostics: {
    candidates: { semantic: 40, lexical: 12, phrase: 0 },
    after_dedupe: 30,
    timings_ms: { embedding: 120, semantic_search: 8 },
    embedding_spec: "Qwen/Qwen3-Embedding-0.6B@1024",
    reranker: "none",
  },
  ...overrides,
});

export const analysis = (overrides: Partial<TicketAnalysis> = {}): TicketAnalysis => ({
  label: "AI-generated draft. Not reviewed or approved by a human.",
  classification: {
    category: "runners",
    confidence: 0.92,
    secondary_category: null,
    secondary_confidence: null,
    ambiguous: false,
    rationale: "Runner TLS problem.",
  },
  priority: {
    level: "high",
    explanation: "High: CI/CD or deployments blocked with no workaround.",
    triggered_factors: ["CI/CD or deployments blocked with no workaround"],
    signals: null,
    is_estimate: true,
  },
  resolution: {
    status: "draft",
    summary: "The runner does not trust the internal CA.",
    steps: [{ number: 1, instruction: "Set `tls-ca-file` in config.toml.", source_ids: ["SRC-001"] }],
    explanation: "The runner must trust the CA.",
    warnings: [],
    questions_for_customer: [],
    confidence: "medium",
  },
  sources: [
    {
      source_id: "SRC-001",
      title: "Self-signed certificates",
      section: "Trusting certificates",
      url: "https://docs.gitlab.com/runner/configuration/tls-self-signed/",
      excerpt: "Set tls-ca-file in config.toml.",
      semantic_score: 0.81,
      final_score: 0.81,
    },
  ],
  grounding: {
    grounded: true,
    score: 1,
    checks: [{ name: "citations_valid", passed: true, detail: "All cited IDs exist in the evidence set." }],
    claim_level: false,
  },
  human_review: { required: true, reasons: ["high_or_critical_priority"] },
  retrieval: {
    method: "hybrid",
    evidence_sufficient: true,
    reason: "ok",
    threshold: 0.6,
    candidates: 8,
    evidence_used: 1,
    excluded_for_injection: [],
    degraded: false,
    warnings: [],
  },
  errors: [],
  timings_ms: { classification: 900, retrieval: 300, generation: 2500, total: 3800 },
  model: { llm: "openai/gpt-oss-120b", embedding: "Qwen/Qwen3-Embedding-0.6B@1024", prompt_versions: {} },
  ...overrides,
});

export const readiness = (overrides: Partial<Readiness> = {}): Readiness => ({
  status: "ok",
  checks: [
    { name: "api", status: "ok", detail: "Serving requests.", latency_ms: null },
    { name: "database", status: "ok", detail: "Connected.", latency_ms: 2 },
  ],
  knowledge_base: { documents: 45, chunks: 900, embedded_chunks: 486 },
  ...overrides,
});

export const me = (role: "admin" | "agent" = "admin", stats: Partial<Me["stats"]> = {}): Me => ({
  user: {
    id: role === "admin" ? "u-admin" : "u-agent",
    email: `${role}@example.test`,
    name: role === "admin" ? "Ada Admin" : "Alex Agent",
    role,
    is_active: true,
    created_at: "2026-10-01T09:00:00Z",
    last_login_at: "2026-10-02T09:00:00Z",
  },
  stats: {
    analyses: 7,
    drafts: 4,
    withheld: 2,
    insufficient_evidence: 1,
    unavailable: 0,
    last_analysis_at: "2026-10-02T09:30:00Z",
    ...stats,
  },
});
