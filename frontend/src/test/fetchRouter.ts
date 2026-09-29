import { vi } from "vitest";

type Route = { method?: string; path: string | RegExp; status?: number; body: unknown; headers?: Record<string, string> };

/** Stub global fetch: each call is answered by the first matching route; unmatched → 404. */
export function stubApi(routes: Route[]) {
  const calls: { method: string; url: string; body: unknown; headers: Record<string, string> }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      method,
      url,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init?.headers as Record<string, string>) ?? {},
    });
    const route = routes.find(
      (r) =>
        (r.method ?? "GET") === method &&
        (typeof r.path === "string" ? url.split("?")[0] === `/api/v1${r.path}` : r.path.test(url)),
    );
    if (!route) {
      return new Response(
        JSON.stringify({ error: { code: "NOT_FOUND", message: "Not Found", details: null, request_id: "r-404" } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(JSON.stringify(route.body), {
      status: route.status ?? 200,
      headers: { "Content-Type": "application/json", "X-Request-ID": "req-test", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export const errorBody = (code: string, message: string, requestId = "req-err") => ({
  error: { code, message, details: null, request_id: requestId },
});
