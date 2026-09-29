import { z } from "zod";

/** Base path for all API calls. Same-origin: nginx (prod) or Vite (dev) proxies it. */
export const API_BASE = "/api/v1";
export const DEFAULT_TIMEOUT_MS = 15_000;

export const ErrorResponseSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.record(z.string(), z.unknown()).nullable().optional(),
    request_id: z.string().nullable().optional(),
  }),
});

export type ApiErrorKind = "http" | "timeout" | "network" | "invalid_response" | "aborted";

/** Every failure surfaced to the UI is an ApiError, so views can render one error shape. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(init: {
    kind: ApiErrorKind;
    message: string;
    code: string;
    status?: number | null;
    requestId?: string | null;
    details?: Record<string, unknown> | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.kind = init.kind;
    this.code = init.code;
    this.status = init.status ?? null;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? null;
  }

  /** The backend (or something between us and it) is unreachable or unhealthy. */
  get isDependencyUnavailable(): boolean {
    return (
      this.kind === "network" ||
      this.kind === "timeout" ||
      this.status === 502 ||
      this.status === 503 ||
      this.status === 504
    );
  }
}

export interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Non-2xx statuses whose body is a normal payload (e.g. 503 from GET /ready). */
  acceptStatuses?: number[];
}

export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    headers = {},
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    acceptStatuses = [],
  } = options;

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort, { once: true });

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (cause) {
    if (timedOut) {
      throw new ApiError({
        kind: "timeout",
        code: "CLIENT_TIMEOUT",
        message: `The server did not respond within ${Math.round(timeoutMs / 1000)} seconds.`,
      });
    }
    if (controller.signal.aborted) {
      throw new ApiError({ kind: "aborted", code: "ABORTED", message: "Request was cancelled." });
    }
    throw new ApiError({
      kind: "network",
      code: "NETWORK_ERROR",
      message: "Could not reach the API. Check that the backend is running.",
      details: { cause: String(cause) },
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }

  const requestId = response.headers.get("X-Request-ID");
  const payload: unknown = await response.json().catch(() => undefined);

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    const parsed = ErrorResponseSchema.safeParse(payload);
    if (parsed.success) {
      const { code, message, details, request_id } = parsed.data.error;
      throw new ApiError({
        kind: "http",
        status: response.status,
        code,
        message,
        details: details ?? null,
        requestId: request_id ?? requestId,
      });
    }
    // Not our contract: usually a proxy error page when the backend is down.
    throw new ApiError({
      kind: "http",
      status: response.status,
      code: response.status >= 500 ? "UPSTREAM_UNAVAILABLE" : "HTTP_ERROR",
      message: `The server returned HTTP ${response.status}.`,
      requestId,
    });
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError({
      kind: "invalid_response",
      status: response.status,
      code: "INVALID_RESPONSE",
      message: "The API returned data in an unexpected format.",
      requestId,
      details: { issues: z.treeifyError(parsed.error) },
    });
  }
  return parsed.data;
}
