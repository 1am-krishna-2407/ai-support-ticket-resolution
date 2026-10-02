/**
 * Zod schemas mirroring the backend response models (backend/app/schemas/*).
 * Every API response is parsed with these, so a contract drift surfaces as an
 * explicit "unexpected format" error instead of a broken screen.
 */
import { z } from "zod";

const nullableString = z.string().nullable();

// --- Retrieval --------------------------------------------------------------------------

export const RetrievalMethodSchema = z.enum(["vector", "lexical", "hybrid"]);
export type RetrievalMethod = z.infer<typeof RetrievalMethodSchema>;

export const RetrievedChunkSchema = z.object({
  source_id: z.string(),
  chunk_id: z.string(),
  document_id: z.string(),
  document_version: z.number(),
  title: z.string(),
  section: nullableString,
  subsection: nullableString,
  heading_path: z.string(),
  url: z.string(),
  category: z.string(),
  content: z.string(),
  token_count: z.number(),
  exact_phrase_match: z.boolean(),
  passes_threshold: z.boolean(),
  scores: z.object({
    semantic: z.number(),
    lexical: z.number(),
    fused: z.number(),
    rerank: z.number().nullable(),
    final: z.number(),
  }),
  ranks: z.object({
    semantic: z.number().nullable(),
    lexical: z.number().nullable(),
    phrase: z.number().nullable(),
  }),
});
export type RetrievedChunk = z.infer<typeof RetrievedChunkSchema>;

export const RetrievalResultSchema = z.object({
  query: z.string(),
  retrieval_query: z.string(),
  extracted_terms: z.record(z.string(), z.array(z.string())),
  phrases: z.array(z.string()),
  method: RetrievalMethodSchema,
  top_k: z.number(),
  threshold: z.number(),
  threshold_basis: z.string(),
  evidence_sufficient: z.boolean(),
  reason: z.string(),
  degraded: z.boolean(),
  warnings: z.array(z.string()),
  results: z.array(RetrievedChunkSchema),
  diagnostics: z.object({
    candidates: z.record(z.string(), z.number()),
    after_dedupe: z.number(),
    timings_ms: z.record(z.string(), z.number()),
    embedding_spec: nullableString,
    reranker: z.string(),
  }),
});
export type RetrievalResult = z.infer<typeof RetrievalResultSchema>;

// --- Ticket analysis ----------------------------------------------------------------------

export const PrioritySignalsSchema = z.object({
  production_impact: z.string(),
  users_affected: z.string(),
  security_concern: z.boolean(),
  data_loss_risk: z.boolean(),
  blocks_ci_cd_or_deployments: z.boolean(),
  workaround_available: z.boolean(),
  time_sensitive: z.boolean(),
  regression: z.boolean(),
});

export const ClassificationSchema = z.object({
  category: nullableString,
  confidence: z.number().nullable(),
  secondary_category: nullableString,
  secondary_confidence: z.number().nullable(),
  ambiguous: z.boolean(),
  rationale: nullableString,
});

export const PrioritySchema = z.object({
  level: z.enum(["low", "medium", "high", "critical"]).nullable(),
  explanation: z.string(),
  triggered_factors: z.array(z.string()),
  signals: PrioritySignalsSchema.nullable(),
  is_estimate: z.boolean(),
});

export const HumanReviewSchema = z.object({
  required: z.boolean(),
  reasons: z.array(z.string()),
});

export const StageErrorSchema = z.object({
  stage: z.string(),
  code: z.string(),
  message: z.string(),
});

export const ModelInfoSchema = z.object({
  llm: nullableString,
  embedding: nullableString,
  prompt_versions: z.record(z.string(), z.string()),
});

export const CitationSchema = z.object({
  source_id: z.string(),
  title: z.string(),
  section: nullableString,
  url: z.string(),
  excerpt: z.string(),
  semantic_score: z.number(),
  final_score: z.number(),
});
export type Citation = z.infer<typeof CitationSchema>;

export const ResolutionStatusSchema = z.enum([
  "draft",
  "insufficient_evidence",
  "withheld",
  "unavailable",
]);

export const TicketAnalysisSchema = z.object({
  label: z.string(),
  analysis_id: z.string().nullable().optional(),
  classification: ClassificationSchema,
  priority: PrioritySchema,
  resolution: z.object({
    status: ResolutionStatusSchema,
    summary: z.string(),
    steps: z.array(
      z.object({ number: z.number(), instruction: z.string(), source_ids: z.array(z.string()) }),
    ),
    explanation: z.string(),
    warnings: z.array(z.string()),
    questions_for_customer: z.array(z.string()),
    confidence: z.enum(["low", "medium", "high"]).nullable(),
  }),
  sources: z.array(CitationSchema),
  grounding: z.object({
    grounded: z.boolean(),
    score: z.number(),
    checks: z.array(z.object({ name: z.string(), passed: z.boolean(), detail: z.string() })),
    claim_level: z.boolean(),
  }),
  human_review: HumanReviewSchema,
  retrieval: z.object({
    method: RetrievalMethodSchema,
    evidence_sufficient: z.boolean(),
    reason: z.string(),
    threshold: z.number(),
    candidates: z.number(),
    evidence_used: z.number(),
    excluded_for_injection: z.array(z.string()),
    degraded: z.boolean(),
    warnings: z.array(z.string()),
  }),
  errors: z.array(StageErrorSchema),
  timings_ms: z.record(z.string(), z.number()),
  model: ModelInfoSchema,
});
export type TicketAnalysis = z.infer<typeof TicketAnalysisSchema>;

// --- Documents ------------------------------------------------------------------------------

export const DocumentSummarySchema = z.object({
  document_id: z.string(),
  title: z.string(),
  canonical_url: z.string(),
  category: z.string(),
  source_type: z.string(),
  license: z.string(),
  version: z.number(),
  status: z.string(),
  enabled: z.boolean(),
  content_hash: z.string(),
  retrieved_at: z.string(),
  published_at: nullableString,
  chunk_count: z.number(),
});
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;

export const DocumentPageSchema = z.object({
  total: z.number(),
  offset: z.number(),
  limit: z.number(),
  items: z.array(DocumentSummarySchema),
  attribution: z.string(),
});
export type DocumentPage = z.infer<typeof DocumentPageSchema>;

export const DocumentDetailSchema = DocumentSummarySchema.extend({
  source_version: nullableString,
  embedding_model: nullableString,
  versions: z.array(
    z.object({
      version: z.number(),
      status: z.string(),
      content_hash: z.string(),
      retrieved_at: z.string(),
      published_at: nullableString,
      chunk_count: z.number(),
      embedding_model: nullableString,
      ingestion_job_id: nullableString,
    }),
  ),
  attribution: z.string(),
});
export type DocumentDetail = z.infer<typeof DocumentDetailSchema>;

export const ChunkPageSchema = z.object({
  document_id: z.string(),
  version: z.number().nullable(),
  total: z.number(),
  offset: z.number(),
  limit: z.number(),
  items: z.array(
    z.object({
      chunk_id: z.string(),
      chunk_index: z.number(),
      document_version: z.number(),
      heading_path: z.string(),
      section: nullableString,
      subsection: nullableString,
      anchor: nullableString,
      content: z.string(),
      token_count: z.number(),
      has_code: z.boolean(),
      is_split_oversize: z.boolean(),
      embedding_model: nullableString,
      content_hash: z.string(),
    }),
  ),
});
export type ChunkPage = z.infer<typeof ChunkPageSchema>;

// --- Ingestion ------------------------------------------------------------------------------

export const IngestionJobSchema = z.object({
  id: z.string(),
  status: z.string(),
  trigger: z.string(),
  config: z.record(z.string(), z.unknown()),
  total_documents: z.number(),
  processed_documents: z.number(),
  changed_documents: z.number(),
  failed_documents: z.number(),
  total_chunks: z.number(),
  embedding_calls: z.number(),
  reused_embeddings: z.number(),
  error_summary: z.array(z.record(z.string(), z.unknown())),
  created_at: z.string(),
  started_at: nullableString,
  finished_at: nullableString,
});
export type IngestionJob = z.infer<typeof IngestionJobSchema>;

export const IngestionStatusSchema = z.object({
  running: z.boolean(),
  latest: IngestionJobSchema.nullable(),
});
export type IngestionStatus = z.infer<typeof IngestionStatusSchema>;

// --- Readiness ------------------------------------------------------------------------------

export const DependencyStatusSchema = z.enum(["ok", "degraded", "unavailable", "not_configured"]);
export type DependencyStatus = z.infer<typeof DependencyStatusSchema>;

export const ReadinessSchema = z.object({
  status: DependencyStatusSchema,
  checks: z.array(
    z.object({
      name: z.string(),
      status: DependencyStatusSchema,
      detail: z.string(),
      latency_ms: z.number().nullable().optional(),
    }),
  ),
  knowledge_base: z
    .object({ documents: z.number(), chunks: z.number(), embedded_chunks: z.number() })
    .nullable(),
});
export type Readiness = z.infer<typeof ReadinessSchema>;

// --- Evaluation -----------------------------------------------------------------------------

export const EvaluationRunSchema = z.object({
  id: z.string(),
  name: nullableString,
  status: z.string(),
  config: z.record(z.string(), z.unknown()),
  metrics: z
    .object({
      cases: z.number(),
      errors: z.number(),
      deterministic: z.record(z.string(), z.number().nullable()),
      latency_ms: z.record(z.string(), z.number().nullable()),
      by_type: z.record(z.string(), z.record(z.string(), z.number().nullable())).optional(),
    })
    .nullable(),
  dataset_version: nullableString,
  total_cases: z.number(),
  completed_cases: z.number(),
  error: nullableString,
  created_at: z.string(),
  started_at: nullableString,
  finished_at: nullableString,
});
export type EvaluationRun = z.infer<typeof EvaluationRunSchema>;

export const EvaluationRunListSchema = z.object({
  items: z.array(EvaluationRunSchema),
  running: z.boolean(),
});

export const EvaluationCaseSchema = z.object({
  case_id: z.string(),
  case_type: z.string(),
  expected: z.record(z.string(), z.unknown()),
  actual: z.record(z.string(), z.unknown()).nullable(),
  metrics: z.record(z.string(), z.unknown()).nullable(),
  passed: z.boolean().nullable(),
  latency_ms: z.number().nullable(),
  error: nullableString,
});
export type EvaluationCase = z.infer<typeof EvaluationCaseSchema>;

export const EvaluationRunDetailSchema = EvaluationRunSchema.extend({
  cases: z.array(EvaluationCaseSchema),
});
export type EvaluationRunDetail = z.infer<typeof EvaluationRunDetailSchema>;

// --- Users, sessions, saved analyses -------------------------------------------------------

export const RoleSchema = z.enum(["admin", "agent"]);
export type Role = z.infer<typeof RoleSchema>;

export const UserSchema = z.object({
  id: z.string(),
  email: z.string(),
  name: z.string(),
  role: RoleSchema,
  is_active: z.boolean(),
  created_at: z.string(),
  last_login_at: z.string().nullable(),
});
export type User = z.infer<typeof UserSchema>;

export const UserStatsSchema = z.object({
  analyses: z.number(),
  drafts: z.number(),
  withheld: z.number(),
  insufficient_evidence: z.number(),
  unavailable: z.number(),
  last_analysis_at: z.string().nullable(),
});
export type UserStats = z.infer<typeof UserStatsSchema>;

export const MeSchema = z.object({ user: UserSchema, stats: UserStatsSchema });
export type Me = z.infer<typeof MeSchema>;

export const UserPageSchema = z.object({
  total: z.number(),
  offset: z.number(),
  limit: z.number(),
  items: z.array(UserSchema.extend({ analyses: z.number() })),
});
export type UserPage = z.infer<typeof UserPageSchema>;

export const TicketHistoryItemSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  title: z.string(),
  status: ResolutionStatusSchema,
  category: z.string().nullable(),
  priority: z.string().nullable(),
  grounded: z.boolean().nullable(),
  latency_ms: z.number().nullable(),
  user_email: z.string(),
});
export type TicketHistoryItem = z.infer<typeof TicketHistoryItemSchema>;

export const TicketHistoryPageSchema = z.object({
  total: z.number(),
  offset: z.number(),
  limit: z.number(),
  scope: z.enum(["mine", "all"]),
  items: z.array(TicketHistoryItemSchema),
});
export type TicketHistoryPage = z.infer<typeof TicketHistoryPageSchema>;

export const TicketHistoryDetailSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  user_email: z.string(),
  title: z.string(),
  description: z.string(),
  environment: z.record(z.string(), z.unknown()).nullable(),
  result: TicketAnalysisSchema,
});
export type TicketHistoryDetail = z.infer<typeof TicketHistoryDetailSchema>;

export const AuthConfigSchema = z.object({
  registration: z.enum(["open", "approval", "closed"]),
  password_min_length: z.number(),
});
export type AuthConfig = z.infer<typeof AuthConfigSchema>;

export const RegisterResultSchema = z.object({
  status: z.enum(["active", "pending"]),
  me: MeSchema.nullable(),
});

