import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../lib/queryClient";
import { routes } from "../routes";
import { errorBody, stubApi } from "../test/fetchRouter";
import { analysis, chunk, me, readiness, retrievalResult } from "../test/fixtures";

const health = { path: "/health", body: { status: "ok" } };

function renderAt(path: string) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
    </QueryClientProvider>,
  );
}

async function fillTicket(title: string, description: string) {
  await userEvent.type(await screen.findByLabelText("Title"), title);
  await userEvent.type(screen.getByLabelText("Description"), description);
  await userEvent.click(screen.getByRole("button", { name: "Analyse ticket" }));
}

describe("Ticket Analysis", () => {
  it("validates input before calling the API", async () => {
    const calls = stubApi([health]);
    renderAt("/tickets");
    await fillTicket("ab", "short");
    expect(screen.getByText("Title needs at least 3 characters.")).toBeInTheDocument();
    expect(screen.getByText("Description needs at least 10 characters.")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/tickets/analyze"))).toHaveLength(0);
  });

  it("shows the draft label, review reasons, cited steps, and sources", async () => {
    const calls = stubApi([health, { method: "POST", path: "/tickets/analyze", body: analysis() }]);
    renderAt("/tickets");
    await fillTicket("Runner TLS error", "x509: certificate signed by unknown authority");
    expect(await screen.findByText("Set `tls-ca-file` in config.toml.")).toBeInTheDocument();
    // Routine review reasons (here: high priority) do not interrupt the result.
    expect(screen.queryByLabelText("Review flags")).not.toBeInTheDocument();
    expect(screen.queryByText(/Not reviewed or approved by a human/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Self-signed certificates" })).toHaveAttribute(
      "href",
      "https://docs.gitlab.com/runner/configuration/tls-self-signed/",
    );
    expect(screen.getByText(/Priority \(AI-assisted estimate\)/)).toBeInTheDocument();
    const sent = calls.find((c) => c.url.includes("/tickets/analyze"));
    expect(sent?.body).toEqual({
      title: "Runner TLS error",
      description: "x509: certificate signed by unknown authority",
      environment: null,
    });
  });

  it("presents insufficient evidence honestly", async () => {
    stubApi([
      health,
      {
        method: "POST",
        path: "/tickets/analyze",
        body: analysis({
          resolution: {
            status: "insufficient_evidence",
            summary: "The knowledge base does not contain enough relevant documentation.",
            steps: [],
            explanation: "",
            warnings: [],
            questions_for_customer: [],
            confidence: null,
          },
          sources: [],
          human_review: { required: true, reasons: ["insufficient_evidence"] },
        }),
      },
    ]);
    renderAt("/tickets");
    await fillTicket("Printer jam", "The office printer jams on double-sided jobs.");
    expect(await screen.findByText("Insufficient documentation")).toBeInTheDocument();
    expect(screen.getByText("No documentation met the relevance threshold.")).toBeInTheDocument();
  });

  it("flags serious review reasons in one line", async () => {
    stubApi([
      health,
      {
        method: "POST",
        path: "/tickets/analyze",
        body: analysis({
          human_review: {
            required: true,
            reasons: ["high_or_critical_priority", "prompt_injection_suspected", "security_concern"],
          },
        }),
      },
    ]);
    renderAt("/tickets");
    await fillTicket("Runner TLS error", "x509: certificate signed by unknown authority");
    const flags = await screen.findByLabelText("Review flags");
    expect(within(flags).getByText("Possible security concern")).toBeInTheDocument();
    expect(within(flags).getByText(/possible prompt injection/)).toBeInTheDocument();
    expect(within(flags).queryByText("High or critical priority")).not.toBeInTheDocument();
  });

  it("shows API errors with the request ID instead of a blank screen", async () => {
    stubApi([
      health,
      {
        method: "POST",
        path: "/tickets/analyze",
        status: 503,
        body: errorBody("RETRIEVAL_UNAVAILABLE", "Retrieval is temporarily unavailable.", "req-503"),
      },
    ]);
    renderAt("/tickets");
    await fillTicket("Runner TLS error", "x509: certificate signed by unknown authority");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Service unavailable")).toBeInTheDocument();
    expect(within(alert).getByText("Request ID: req-503")).toBeInTheDocument();
  });

  it("tells the user how long to wait when rate limited", async () => {
    const body = errorBody("RATE_LIMITED", "Too many requests. Please retry later.", "req-429");
    body.error.details = { retry_after_seconds: 42, group: "analyze" } as unknown as null;
    stubApi([health, { method: "POST", path: "/tickets/analyze", status: 429, body }]);
    renderAt("/tickets");
    await fillTicket("Runner TLS error", "x509: certificate signed by unknown authority");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Too many requests")).toBeInTheDocument();
    expect(within(alert).getByText("Please wait 42 s and try again.")).toBeInTheDocument();
  });
});

describe("Retrieval Explorer", () => {
  it("renders ranked results with score breakdowns and threshold state", async () => {
    stubApi([
      health,
      {
        method: "POST",
        path: "/search",
        body: retrievalResult({
          results: [
            chunk(),
            chunk({ source_id: "SRC-002", chunk_id: "c2", passes_threshold: false, title: "Other page" }),
          ],
        }),
      },
    ]);
    renderAt("/retrieval");
    await userEvent.type(await screen.findByLabelText("Query"), "x509");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Relevant evidence found")).toBeInTheDocument();
    const results = screen.getByRole("list", { name: "Results" });
    expect(within(results).getAllByRole("listitem")).toHaveLength(2);
    expect(within(results).getByText("below threshold")).toBeInTheDocument();
    await userEvent.click(within(results).getAllByRole("button", { name: "Show chunk content" })[0]!);
    expect(screen.getAllByText("Set tls-ca-file in config.toml.").length).toBeGreaterThan(0);
  });

  it("explains degraded search", async () => {
    stubApi([
      health,
      {
        method: "POST",
        path: "/search",
        body: retrievalResult({
          degraded: true,
          evidence_sufficient: false,
          warnings: ["Embeddings unavailable; used full-text and exact-phrase search."],
          results: [],
        }),
      },
    ]);
    renderAt("/retrieval");
    await userEvent.type(await screen.findByLabelText("Query"), "x509");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Insufficient evidence")).toBeInTheDocument();
    expect(screen.getByText(/Embeddings unavailable/)).toBeInTheDocument();
    expect(screen.getByText("No matching documentation.")).toBeInTheDocument();
  });
});

describe("Knowledge Base", () => {
  it("guides the user when the knowledge base is empty", async () => {
    stubApi([
      health,
      { path: "/documents", body: { total: 0, offset: 0, limit: 25, items: [], attribution: "x" } },
    ]);
    renderAt("/knowledge-base");
    expect(await screen.findByText(/The knowledge base is empty/)).toBeInTheDocument();
  });

  it("lists documents with links to details", async () => {
    stubApi([
      health,
      {
        path: "/documents",
        body: {
          total: 1,
          offset: 0,
          limit: 25,
          attribution: "Content from GitLab Docs",
          items: [
            {
              document_id: "ci-variables", title: "CI/CD variables",
              canonical_url: "https://docs.gitlab.com/ci/variables/", category: "ci_cd",
              source_type: "product_documentation", license: "CC BY-SA 4.0", version: 2,
              status: "active", enabled: true, content_hash: "abc", retrieved_at: "2026-09-29T10:00:00Z",
              published_at: null, chunk_count: 25,
            },
          ],
        },
      },
    ]);
    renderAt("/knowledge-base");
    const link = await screen.findByRole("link", { name: "CI/CD variables" });
    expect(link).toHaveAttribute("href", "/knowledge-base/ci-variables");
    expect(screen.getByText("Content from GitLab Docs")).toBeInTheDocument();
  });
});

describe("Ingestion", () => {
  it("lets admins start a job with their session and no token", async () => {
    const calls = stubApi([
      health,
      { path: "/ingestion/status", body: { running: false, latest: null } },
      { method: "POST", path: "/ingestion/run", status: 403, body: errorBody("FORBIDDEN", "This requires an admin account.") },
    ]);
    renderAt("/ingestion");
    expect(await screen.findByText("No ingestion job has run yet.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Admin token")).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Start ingestion" });
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(await screen.findByText("Your account is not allowed to start ingestion.")).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/ingestion/run"));
    expect(sent?.headers["X-Admin-Token"]).toBeUndefined();
  });

  it("explains to agents that only admins can start a job", async () => {
    stubApi([
      health,
      { path: "/auth/me", body: me("agent") },
      { path: "/ingestion/status", body: { running: false, latest: null } },
    ]);
    renderAt("/ingestion");
    expect(await screen.findByText("Only admins can start ingestion.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start ingestion" })).toBeDisabled();
  });
});

describe("History", () => {
  const item = {
    id: "a1",
    created_at: "2026-10-02T09:30:00Z",
    title: "Runner TLS error",
    status: "draft",
    category: "runners",
    priority: "high",
    grounded: true,
    latency_ms: 84000,
    user_email: "admin@example.test",
  };

  it("lists saved analyses and opens one", async () => {
    stubApi([
      health,
      { path: "/tickets/history", body: { total: 1, offset: 0, limit: 20, scope: "mine", items: [item] } },
      {
        path: "/tickets/history/a1",
        body: {
          id: "a1",
          created_at: item.created_at,
          user_email: item.user_email,
          title: item.title,
          description: "x509: certificate signed by unknown authority",
          environment: { deployment: "self-managed" },
          result: analysis({ analysis_id: "a1" }),
        },
      },
    ]);
    renderAt("/history");
    await userEvent.click(await screen.findByRole("link", { name: "Runner TLS error" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Runner TLS error" })).toBeInTheDocument();
    expect(screen.getByText("x509: certificate signed by unknown authority")).toBeInTheDocument();
    expect(screen.getByText("Set `tls-ca-file` in config.toml.")).toBeInTheDocument();
  });

  it("lets admins switch to every user's analyses", async () => {
    const calls = stubApi([
      health,
      { path: "/tickets/history", body: { total: 1, offset: 0, limit: 20, scope: "mine", items: [item] } },
    ]);
    renderAt("/history");
    await userEvent.click(await screen.findByRole("button", { name: "All users" }));
    expect(calls.some((c) => c.url.includes("/tickets/history?scope=all"))).toBe(true);
  });

  it("has no scope switch for agents and an empty state", async () => {
    stubApi([
      health,
      { path: "/auth/me", body: me("agent") },
      { path: "/tickets/history", body: { total: 0, offset: 0, limit: 20, scope: "mine", items: [] } },
    ]);
    renderAt("/history");
    expect(await screen.findByText("No saved analyses yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "All users" })).not.toBeInTheDocument();
  });
});

describe("System Status and Dashboard", () => {
  it("shows an unavailable system from a 503 readiness response", async () => {
    stubApi([
      health,
      {
        path: "/ready",
        status: 503,
        body: readiness({
          status: "unavailable",
          checks: [{ name: "database", status: "unavailable", detail: "Cannot reach database.", latency_ms: null }],
          knowledge_base: null,
        }),
      },
    ]);
    renderAt("/status");
    expect(await screen.findByText("Cannot reach database.")).toBeInTheDocument();
    expect(screen.getAllByText("unavailable").length).toBeGreaterThan(0);
  });

  it("shows real knowledge-base figures and no invented evaluation numbers", async () => {
    stubApi([
      health,
      { path: "/ready", body: readiness() },
      { path: "/ingestion/status", body: { running: false, latest: null } },
    ]);
    renderAt("/");
    expect(await screen.findByText("486")).toBeInTheDocument();
    expect(screen.getByText("54% searchable by meaning")).toBeInTheDocument();
    expect(screen.getByText("No evaluation results available yet.")).toBeInTheDocument();
  });
});

const evalRun = (id: string, overrides: Record<string, unknown> = {}) => ({
  id,
  name: `run-${id}`,
  status: "completed",
  config: { method: "hybrid", k: 5, generation: false },
  metrics: {
    cases: 3,
    errors: 0,
    deterministic: { "recall@5": 0.8, "hit_rate@5": 1, mrr: 0.75, correct_refusal_rate: 1, false_refusal_rate: 0, category_accuracy: null },
    latency_ms: { mean: 120, median: 110, p95: 300 },
    by_type: { runners: { "hit_rate@5": 1, mrr: 1, correct_refusal_rate: null } },
  },
  dataset_version: "1",
  total_cases: 3,
  completed_cases: 3,
  error: null,
  created_at: "2026-09-30T10:00:00Z",
  started_at: null,
  finished_at: null,
  ...overrides,
});

describe("Evaluation", () => {
  it("shows stored metrics, comparison, and failed cases", async () => {
    const a = evalRun("aaaaaaaa");
    const b = evalRun("bbbbbbbb", { config: { method: "vector", k: 5, generation: false } });
    stubApi([
      health,
      { path: "/evaluations", body: { items: [a, b], running: false } },
      {
        path: /\/evaluations\/aaaaaaaa$/,
        body: {
          ...a,
          cases: [
            { case_id: "c1", case_type: "runners", expected: { title: "Register" }, actual: {}, metrics: {}, passed: true, latency_ms: 100, error: null },
            { case_id: "c2", case_type: "runners", expected: { title: "Offline runner" }, actual: { retrieval: { documents: ["x"] } }, metrics: {}, passed: false, latency_ms: 120, error: null },
          ],
        },
      },
      { path: /\/evaluations\/bbbbbbbb$/, body: { ...b, metrics: { ...b.metrics, deterministic: { ...b.metrics.deterministic, mrr: 0.5 } }, cases: [] } },
    ]);
    renderAt("/evaluation");
    expect(await screen.findByText("Failed cases (1 of 2)")).toBeInTheDocument();
    expect(screen.getAllByText(/Offline runner/).length).toBeGreaterThan(0);
    await userEvent.selectOptions(screen.getByLabelText(/Compare with/), "bbbbbbbb");
    expect(await screen.findByText("+0.250")).toBeInTheDocument(); // MRR 0.75 vs 0.50
  });

  it("says so when no runs exist", async () => {
    stubApi([health, { path: "/evaluations", body: { items: [], running: false } }]);
    renderAt("/evaluation");
    expect(await screen.findAllByText(/No evaluation results available yet/)).not.toHaveLength(0);
  });
});
