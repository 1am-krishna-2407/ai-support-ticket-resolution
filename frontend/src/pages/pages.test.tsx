import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../lib/queryClient";
import { routes } from "../routes";
import { errorBody, stubApi } from "../test/fetchRouter";
import { analysis, chunk, readiness, retrievalResult } from "../test/fixtures";

const health = { path: "/health", body: { status: "ok" } };

function renderAt(path: string) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
    </QueryClientProvider>,
  );
}

async function fillTicket(title: string, description: string) {
  await userEvent.type(screen.getByLabelText("Title"), title);
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
    expect(await screen.findByText("AI-generated draft. Not reviewed or approved by a human.")).toBeInTheDocument();
    expect(screen.getByText("High or critical priority")).toBeInTheDocument();
    expect(screen.getByText("Set `tls-ca-file` in config.toml.")).toBeInTheDocument();
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
    expect(screen.getByText("Documentation does not cover this ticket well enough")).toBeInTheDocument();
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
    await userEvent.type(screen.getByLabelText("Query"), "x509");
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
    await userEvent.type(screen.getByLabelText("Query"), "x509");
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
  it("disables the trigger without an admin token and reports a rejected token", async () => {
    stubApi([
      health,
      { path: "/ingestion/status", body: { running: false, latest: null } },
      { method: "POST", path: "/ingestion/run", status: 403, body: errorBody("FORBIDDEN", "The admin token is invalid.") },
    ]);
    renderAt("/ingestion");
    expect(await screen.findByText("No ingestion job has run yet.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Start ingestion" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Admin token"), "wrong-token");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(await screen.findByText("The admin token was rejected.")).toBeInTheDocument();
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
