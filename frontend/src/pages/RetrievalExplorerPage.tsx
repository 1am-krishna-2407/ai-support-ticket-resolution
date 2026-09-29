import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { PageHeader } from "../components/PageHeader";
import { ScoreBar } from "../components/ScoreBar";
import { ATTRIBUTION_NOTE } from "../components/SourceCard";
import { ErrorState, LoadingState, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { search, type SearchPayload } from "../services/endpoints";
import type { RetrievalMethod, RetrievalResult, RetrievedChunk } from "../services/schemas";

const QUERY_MAX = 4000;

export function RetrievalExplorerPage() {
  const [query, setQuery] = useState("");
  const [method, setMethod] = useState<RetrievalMethod>("hybrid");
  const [topK, setTopK] = useState(8);
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: SearchPayload) => search(payload) });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) {
      setError("Enter a query.");
      return;
    }
    setError(null);
    mutation.mutate({ query: query.trim(), method, top_k: topK });
  }

  return (
    <>
      <PageHeader
        title="Retrieval Explorer"
        description="Search the knowledge base with vector, lexical, or hybrid retrieval and inspect every score behind the ranking."
      />
      <form onSubmit={onSubmit} noValidate className="mb-6 flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4" aria-label="Search">
        <label className="min-w-64 flex-1 text-sm font-medium text-slate-800">
          Query
          <input
            value={query}
            maxLength={QUERY_MAX}
            onChange={(e) => setQuery(e.target.value)}
            aria-invalid={Boolean(error)}
            placeholder='e.g. x509: certificate signed by unknown authority'
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-normal"
          />
        </label>
        <label className="text-sm font-medium text-slate-800">
          Method
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value as RetrievalMethod)}
            className="mt-1 block rounded-md border border-slate-300 px-2 py-2 text-sm font-normal"
          >
            <option value="hybrid">Hybrid</option>
            <option value="vector">Vector</option>
            <option value="lexical">Lexical</option>
          </select>
        </label>
        <label className="text-sm font-medium text-slate-800">
          Top K
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            className="mt-1 block w-20 rounded-md border border-slate-300 px-2 py-2 text-sm font-normal"
          />
        </label>
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:bg-slate-400"
        >
          {mutation.isPending ? "Searching…" : "Search"}
        </button>
        {error ? <p className="w-full text-xs text-red-700">{error}</p> : null}
      </form>

      <div aria-live="polite">
        {mutation.isPending ? (
          <LoadingState label="Searching…" />
        ) : mutation.isError ? (
          <ErrorState error={mutation.error} />
        ) : mutation.data ? (
          <SearchResults result={mutation.data} />
        ) : (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
            Run a search to see ranked results.
          </p>
        )}
      </div>
    </>
  );
}

export function SearchResults({ result }: { result: RetrievalResult }) {
  const terms = Object.entries(result.extracted_terms);
  return (
    <div className="space-y-4">
      <section
        className={`rounded-lg border px-4 py-3 text-sm ${
          result.evidence_sufficient ? "border-emerald-200 bg-emerald-50" : "border-amber-300 bg-amber-50"
        }`}
      >
        <p className="font-medium">
          {result.evidence_sufficient ? "Relevant evidence found" : "Insufficient evidence"}
        </p>
        <p className="mt-0.5 text-slate-700">{result.reason}</p>
        {result.warnings.map((w) => (
          <p key={w} className="mt-1 text-amber-900">
            {w}
          </p>
        ))}
      </section>

      <Panel title="Query diagnostics">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-slate-500">Retrieval query</dt>
          <dd className="whitespace-pre-line font-mono">{result.retrieval_query}</dd>
          <dt className="text-slate-500">Extracted terms</dt>
          <dd>
            {terms.length
              ? terms.map(([kind, values]) => `${kind}: ${values.join(", ")}`).join(" · ")
              : "none"}
          </dd>
          <dt className="text-slate-500">Exact phrases</dt>
          <dd>{result.phrases.length ? result.phrases.join(" · ") : "none"}</dd>
          <dt className="text-slate-500">Candidates</dt>
          <dd>
            {Object.entries(result.diagnostics.candidates)
              .map(([list, n]) => `${list} ${n}`)
              .join(" · ")}{" "}
            → {result.diagnostics.after_dedupe} after dedupe
          </dd>
          <dt className="text-slate-500">Threshold</dt>
          <dd>
            {result.threshold} on {result.threshold_basis.replace(/_/g, " ")}
          </dd>
          <dt className="text-slate-500">Timings</dt>
          <dd className="font-mono">
            {Object.entries(result.diagnostics.timings_ms)
              .map(([stage, ms]) => `${stage} ${Math.round(ms)} ms`)
              .join(" · ")}
          </dd>
        </dl>
      </Panel>

      {result.results.length ? (
        <ol className="space-y-2" aria-label="Results">
          {result.results.map((chunk) => (
            <ResultRow key={chunk.chunk_id} chunk={chunk} threshold={result.threshold} />
          ))}
        </ol>
      ) : (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-8 text-center text-sm text-slate-600">
          No matching documentation.
        </p>
      )}
      {result.results.length ? <p className="text-xs text-slate-500">{ATTRIBUTION_NOTE}</p> : null}
    </div>
  );
}

function ResultRow({ chunk, threshold }: { chunk: RetrievedChunk; threshold: number }) {
  const [open, setOpen] = useState(false);
  const contentId = `content-${chunk.chunk_id}`;
  return (
    <li className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <span className="mr-2 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{chunk.source_id}</span>
          <a href={chunk.url} target="_blank" rel="noopener noreferrer" className="text-sm font-medium text-blue-700 underline">
            {chunk.title}
          </a>
          <p className="mt-0.5 truncate text-xs text-slate-500">{chunk.heading_path}</p>
        </div>
        <div className="flex gap-1">
          <StatusBadge value={chunk.category} tone="neutral" />
          {chunk.exact_phrase_match ? <StatusBadge value="phrase" label="exact phrase" tone="info" /> : null}
          <StatusBadge
            value={chunk.passes_threshold ? "ok" : "below"}
            label={chunk.passes_threshold ? "evidence" : "below threshold"}
            tone={chunk.passes_threshold ? "good" : "warn"}
          />
        </div>
      </div>
      <div className="mt-2 grid gap-1 sm:grid-cols-2">
        <ScoreBar label="semantic" value={chunk.scores.semantic} threshold={threshold} />
        <ScoreBar label="lexical" value={chunk.scores.lexical} />
        <ScoreBar label="final" value={chunk.scores.final} threshold={threshold} />
        <ScoreBar label="rerank" value={chunk.scores.rerank} />
      </div>
      <p className="mt-1 font-mono text-xs text-slate-500">
        RRF {chunk.scores.fused.toFixed(4)} · ranks semantic {chunk.ranks.semantic ?? "–"}, lexical{" "}
        {chunk.ranks.lexical ?? "–"}, phrase {chunk.ranks.phrase ?? "–"} · {chunk.token_count} tokens
      </p>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={contentId}
        className="mt-2 text-xs font-medium text-blue-700 underline"
      >
        {open ? "Hide chunk content" : "Show chunk content"}
      </button>
      {open ? (
        <pre id={contentId} className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-800">
          {chunk.content}
        </pre>
      ) : null}
    </li>
  );
}
