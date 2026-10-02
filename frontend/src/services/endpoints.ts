/** Typed calls for every backend route the UI uses, plus query-key conventions. */
import { z } from "zod";

import { apiRequest } from "./api";
import {
  AuthConfigSchema,
  ChunkPageSchema,
  DocumentDetailSchema,
  EvaluationRunDetailSchema,
  EvaluationRunListSchema,
  EvaluationRunSchema,
  DocumentPageSchema,
  IngestionJobSchema,
  IngestionStatusSchema,
  MeSchema,
  ReadinessSchema,
  RegisterResultSchema,
  RetrievalResultSchema,
  TicketAnalysisSchema,
  TicketHistoryDetailSchema,
  TicketHistoryPageSchema,
  UserPageSchema,
  UserSchema,
  type RetrievalMethod,
  type Role,
} from "./schemas";

// Analysis runs triage and a reasoning draft (sometimes a correction); real runs take
// 30 s to 3 min on free endpoints, so the client waits up to 5 minutes.
const ANALYSIS_TIMEOUT_MS = 300_000;

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
  me: ["auth", "me"] as const,
  authConfig: ["auth", "config"] as const,
  users: ["users", "list"] as const,
  history: (scope: "mine" | "all", offset: number) => ["history", "list", scope, offset] as const,
  historyItem: (id: string) => ["history", "detail", id] as const,
};

// 204 responses carry no body.
const NoContent = z.unknown();

export const login = (email: string, password: string) =>
  apiRequest("/auth/login", MeSchema, { method: "POST", body: { email, password } });

export const getAuthConfig = (signal?: AbortSignal) =>
  apiRequest("/auth/config", AuthConfigSchema, { signal });

export const register = (user: { email: string; name: string; password: string }) =>
  apiRequest("/auth/register", RegisterResultSchema, { method: "POST", body: user });

export const logout = () => apiRequest("/auth/logout", NoContent, { method: "POST" });

export const getMe = (signal?: AbortSignal) => apiRequest("/auth/me", MeSchema, { signal });

export const changePassword = (currentPassword: string, newPassword: string) =>
  apiRequest("/auth/password", NoContent, {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });

export const listUsers = (signal?: AbortSignal) =>
  apiRequest("/users?limit=200", UserPageSchema, { signal });

export interface NewUser {
  email: string;
  name: string;
  password: string;
  role: Role;
}

export const createUser = (user: NewUser) =>
  apiRequest("/users", UserSchema, { method: "POST", body: user });

export interface UserChanges {
  name?: string;
  role?: Role;
  is_active?: boolean;
  password?: string;
}

export const updateUser = (id: string, changes: UserChanges) =>
  apiRequest(`/users/${encodeURIComponent(id)}`, UserSchema, { method: "PATCH", body: changes });

export const HISTORY_PAGE_SIZE = 20;

export const listHistory = (scope: "mine" | "all", offset: number, signal?: AbortSignal) =>
  apiRequest(
    `/tickets/history?scope=${scope}&offset=${offset}&limit=${HISTORY_PAGE_SIZE}`,
    TicketHistoryPageSchema,
    { signal },
  );

export const getHistoryItem = (id: string, signal?: AbortSignal) =>
  apiRequest(`/tickets/history/${encodeURIComponent(id)}`, TicketHistoryDetailSchema, { signal });

export const deleteHistoryItem = (id: string) =>
  apiRequest(`/tickets/history/${encodeURIComponent(id)}`, NoContent, { method: "DELETE" });

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

// Admin operations use the signed-in admin's session (no token in the browser).
export const startIngestion = (force: boolean) =>
  apiRequest("/ingestion/run", IngestionJobSchema, { method: "POST", body: { force, only: [] } });

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

export const startEvaluation = (payload: EvaluationStartPayload) =>
  apiRequest("/evaluations/run", EvaluationRunSchema, { method: "POST", body: payload });
