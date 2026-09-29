import { describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { ApiError, apiRequest } from "./api";

const Schema = z.object({ status: z.literal("ok") });

function mockFetch(impl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

async function captureError(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected promise to reject");
}

describe("apiRequest", () => {
  it("parses a valid response against the schema", async () => {
    const fetchFn = mockFetch(async () => json({ status: "ok" }));
    await expect(apiRequest("/health", Schema)).resolves.toEqual({ status: "ok" });
    expect(fetchFn).toHaveBeenCalledWith("/api/v1/health", expect.objectContaining({ method: "GET" }));
  });

  it("sends JSON bodies for POST", async () => {
    const fetchFn = mockFetch(async () => json({ status: "ok" }));
    await apiRequest("/x", Schema, { method: "POST", body: { a: 1 } });
    const init = fetchFn.mock.calls[0]?.[1];
    expect(init?.body).toBe('{"a":1}');
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("decodes the backend error contract", async () => {
    mockFetch(async () =>
      json(
        {
          error: {
            code: "DOCUMENT_NOT_FOUND",
            message: "Document not found.",
            details: { document_id: "x" },
            request_id: "req-1",
          },
        },
        404,
      ),
    );
    const err = await captureError(apiRequest("/documents/x", Schema));
    expect(err).toMatchObject({
      kind: "http",
      status: 404,
      code: "DOCUMENT_NOT_FOUND",
      requestId: "req-1",
      details: { document_id: "x" },
    });
    expect(err.isDependencyUnavailable).toBe(false);
  });

  it("treats a non-contract 502 (proxy page) as upstream unavailable", async () => {
    mockFetch(async () => new Response("<html>Bad Gateway</html>", { status: 502 }));
    const err = await captureError(apiRequest("/health", Schema));
    expect(err.code).toBe("UPSTREAM_UNAVAILABLE");
    expect(err.isDependencyUnavailable).toBe(true);
  });

  it("rejects responses that do not match the schema", async () => {
    mockFetch(async () => json({ status: "degraded" }, 200, { "X-Request-ID": "req-2" }));
    const err = await captureError(apiRequest("/health", Schema));
    expect(err).toMatchObject({ kind: "invalid_response", code: "INVALID_RESPONSE", requestId: "req-2" });
  });

  it("reports network failures", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await captureError(apiRequest("/health", Schema));
    expect(err).toMatchObject({ kind: "network", code: "NETWORK_ERROR" });
    expect(err.isDependencyUnavailable).toBe(true);
  });

  it("times out slow requests", async () => {
    mockFetch(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const err = await captureError(apiRequest("/health", Schema, { timeoutMs: 20 }));
    expect(err).toMatchObject({ kind: "timeout", code: "CLIENT_TIMEOUT" });
  });

  it("distinguishes caller cancellation from timeout", async () => {
    mockFetch(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const controller = new AbortController();
    const pending = apiRequest("/health", Schema, { signal: controller.signal });
    controller.abort();
    const err = await captureError(pending);
    expect(err.kind).toBe("aborted");
  });
});
