/** Typed calls for every backend route the UI uses, plus query-key conventions. */
import { apiRequest } from "./api";
import {
  ChunkPageSchema,
  DocumentDetailSchema,
  EvaluationRunDetailSchema,
  EvaluationRunListSchema,
  EvaluationRunSchema,
  DocumentPageSchema,
  IngestionJobSchema,
  IngestionStatusSchema,
  ReadinessSchema,
  RetrievalResultSchema,
  TicketAnalysisSchema,
  type RetrievalMethod,
} from "./schemas";

// Analysis calls an LLM (and may retry once), so it gets a longer client timeout.
const ANALYSIS_TIMEOUT_MS = 120_000;

export interface TicketPayload {
  title: string;
  description: string;
  environment?: {
    gitlab_version?: string;
    deployment?: "gitlab.com" | "self-managed" | "dedicated";
    runner_executor?: string;
    component?: string;
  } | null;
}

export interface SearchPayload {
  query: string;
  method: RetrievalMethod;
  top_k?: number;
  categories?: string[];
}

export interface DocumentQuery {
  search?: string;
  category?: string;
  all_statuses?: boolean;
  offset: number;
  limit: number;
}

export const queryKeys = {
  readiness: ["system", "ready"] as const,
  documents: (q: DocumentQuery) => ["documents", "list", q] as const,
  document: (id: string) => ["documents", "detail", id] as const,
  chunks: (id: string, offset: number, limit: number, version?: number) =>
    ["documents", "chunks", id, { offset, limit, version }] as const,
  ingestionStatus: ["ingestion", "status"] as const,
  evaluations: ["evaluations", "list"] as const,
  evaluation: (id: string) => ["evaluations", "detail", id] as const,
};

export const analyzeTicket = (payload: TicketPayload, signal?: AbortSignal) =>
  apiRequest("/tickets/analyze", TicketAnalysisSchema, {
    method: "POST",
    body: payload,
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  });

export const search = (payload: SearchPayload, signal?: AbortSignal) =>
  apiRequest("/search", RetrievalResultSchema, { method: "POST", body: payload, signal });

export const getReadiness = (signal?: AbortSignal) =>
  apiRequest("/ready", ReadinessSchema, { signal, acceptStatuses: [503] });

function toQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const listDocuments = (q: DocumentQuery, signal?: AbortSignal) =>
  apiRequest(`/documents${toQuery({ ...q })}`, DocumentPageSchema, { signal });

export const getDocument = (documentId: string, signal?: AbortSignal) =>
  apiRequest(`/documents/${encodeURIComponent(documentId)}`, DocumentDetailSchema, { signal });

export const listChunks = (
  documentId: string,
  offset: number,
  limit: number,
  version?: number,
  signal?: AbortSignal,
) =>
  apiRequest(
    `/documents/${encodeURIComponent(documentId)}/chunks${toQuery({ offset, limit, version })}`,
    ChunkPageSchema,
    { signal },
  );

export const getIngestionStatus = (signal?: AbortSignal) =>
  apiRequest("/ingestion/status", IngestionStatusSchema, { signal });

export const startIngestion = (adminToken: string, force: boolean) =>
  apiRequest("/ingestion/run", IngestionJobSchema, {
    method: "POST",
    body: { force, only: [] },
    headers: { "X-Admin-Token": adminToken },
  });

export interface EvaluationStartPayload {
  name?: string;
  method: RetrievalMethod;
  top_k?: number;
  k: number;
  generation: boolean;
}

export const listEvaluations = (signal?: AbortSignal) =>
  apiRequest("/evaluations?limit=50", EvaluationRunListSchema, { signal });

export const getEvaluation = (id: string, signal?: AbortSignal) =>
  apiRequest(`/evaluations/${encodeURIComponent(id)}`, EvaluationRunDetailSchema, { signal });

export const startEvaluation = (adminToken: string, payload: EvaluationStartPayload) =>
  apiRequest("/evaluations/run", EvaluationRunSchema, {
    method: "POST",
    body: payload,
    headers: { "X-Admin-Token": adminToken },
  });
