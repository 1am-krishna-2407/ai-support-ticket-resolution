import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, MetricCard, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { ApiError } from "../services/api";
import {
  getEvaluation,
  listEvaluations,
  queryKeys,
  startEvaluation,
} from "../services/endpoints";
import type { EvaluationRun, EvaluationRunDetail, RetrievalMethod } from "../services/schemas";

const METRIC_LABELS: Record<string, string> = {
  mrr: "MRR",
  category_accuracy: "Category accuracy",
  priority_exact: "Priority (exact)",
  priority_within_one: "Priority (within one level)",
  schema_validity_rate: "Schema validity",
  grounded_rate: "Grounded rate",
  citation_precision: "Citation precision",
  correct_refusal_rate: "Correct refusals (out of scope)",
  false_refusal_rate: "False refusals (answerable)",
  injection_resisted_rate: "Injection resisted",
};

function metricLabel(key: string): string {
  if (METRIC_LABELS[key]) return METRIC_LABELS[key];
  const m = /^(recall|hit_rate|precision)@(\d+)$/.exec(key);
  if (m) return `${m[1] === "hit_rate" ? "Hit rate" : m[1]![0]!.toUpperCase() + m[1]!.slice(1)}@${m[2]}`;
  return key.replace(/_/g, " ");
}

const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(3));

function runLabel(run: EvaluationRun): string {
  const method = String(run.config.method ?? "");
  const gen = run.config.generation ? " + generation" : "";
  return `${run.name ?? run.id.slice(0, 8)} · ${method}${gen}`;
}

export function EvaluationPage() {
  const queryClient = useQueryClient();
  const runs = useQuery({
    queryKey: queryKeys.evaluations,
    queryFn: ({ signal }) => listEvaluations(signal),
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [compareWith, setCompareWith] = useState<string | null>(null);

  const items = runs.data?.items ?? [];
  const selectedId = selected ?? items.find((r) => r.status === "completed")?.id ?? null;

  return (
    <>
      <PageHeader
        title="Evaluation"
        description="Retrieval and answer quality measured on a fixed test dataset. Every figure comes from a stored run; all metrics are computed deterministically (none are LLM-judged)."
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <div className="space-y-4">
          <StartForm
            running={runs.data?.running ?? false}
            onStarted={() => void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations })}
          />
          <Panel title="Runs">
            {runs.isPending ? (
              <LoadingState />
            ) : runs.isError ? (
              <ErrorState error={runs.error} />
            ) : items.length === 0 ? (
              <p className="text-sm text-slate-600">No evaluation results available yet.</p>
            ) : (
              <ul className="space-y-1" aria-label="Evaluation runs">
                {items.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(run.id)}
                      aria-pressed={selectedId === run.id}
                      className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${
                        selectedId === run.id ? "bg-blue-50 text-blue-900" : "hover:bg-slate-50"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate">{runLabel(run)}</span>
                        <StatusBadge value={run.status} />
                      </span>
                      <span className="text-xs text-slate-500">
                        {new Date(run.created_at).toLocaleString()} · {run.completed_cases}/{run.total_cases} cases
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          {selectedId ? (
            <RunDetail
              id={selectedId}
              compareOptions={items.filter((r) => r.id !== selectedId && r.status === "completed")}
              compareWith={compareWith}
              onCompare={setCompareWith}
            />
          ) : (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
              No evaluation results available yet. Start a run to measure retrieval quality.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function StartForm({ running, onStarted }: { running: boolean; onStarted: () => void }) {
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [method, setMethod] = useState<RetrievalMethod>("hybrid");
  const [generation, setGeneration] = useState(false);
  const start = useMutation({
    mutationFn: () =>
      startEvaluation(token, { name: name.trim() || undefined, method, k: 5, generation }),
    onSuccess: onStarted,
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (token) start.mutate();
  }

  return (
    <Panel title="Start a run">
      <form onSubmit={onSubmit} className="space-y-2 text-sm" aria-label="Start evaluation">
        <label className="block font-medium text-slate-800">
          Name (optional)
          <input
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 font-normal"
          />
        </label>
        <label className="block font-medium text-slate-800">
          Retrieval method
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value as RetrievalMethod)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 font-normal"
          >
            <option value="hybrid">Hybrid</option>
            <option value="vector">Vector</option>
            <option value="lexical">Lexical</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-slate-800">
          <input type="checkbox" checked={generation} onChange={(e) => setGeneration(e.target.checked)} />
          Include generation (2 LLM calls per case)
        </label>
        <label className="block font-medium text-slate-800">
          Admin token
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 font-normal"
          />
        </label>
        <button
          type="submit"
          disabled={!token || running || start.isPending}
          className="w-full rounded-md bg-blue-700 px-3 py-2 font-medium text-white hover:bg-blue-800 disabled:bg-slate-400"
        >
          {running ? "A run is in progress" : start.isPending ? "Starting…" : "Start evaluation"}
        </button>
        {!token ? <p className="text-xs text-slate-500">Starting a run requires the admin token.</p> : null}
        {start.isError ? (
          start.error instanceof ApiError &&
          (start.error.code === "UNAUTHORIZED" || start.error.code === "FORBIDDEN") ? (
            <p role="alert" className="text-xs text-red-700">The admin token was rejected.</p>
          ) : (
            <ErrorState error={start.error} />
          )
        ) : null}
      </form>
    </Panel>
  );
}

function RunDetail({
  id,
  compareOptions,
  compareWith,
  onCompare,
}: {
  id: string;
  compareOptions: EvaluationRun[];
  compareWith: string | null;
  onCompare: (id: string | null) => void;
}) {
  const run = useQuery({ queryKey: queryKeys.evaluation(id), queryFn: ({ signal }) => getEvaluation(id, signal) });
  const other = useQuery({
    queryKey: queryKeys.evaluation(compareWith ?? "none"),
    queryFn: ({ signal }) => getEvaluation(compareWith!, signal),
    enabled: Boolean(compareWith),
  });

  if (run.isPending) return <LoadingState />;
  if (run.isError) return <ErrorState error={run.error} />;
  const r = run.data;
  if (r.status === "failed") {
    return <ErrorState error={new Error(`This run failed: ${r.error ?? "unknown error"}`)} />;
  }
  if (!r.metrics) {
    return (
      <Panel title="Run in progress">
        <p className="text-sm text-slate-600">
          {r.completed_cases} of {r.total_cases} cases evaluated…
        </p>
      </Panel>
    );
  }
  return (
    <>
      <Headline run={r} />
      <Panel
        title="Metrics"
        actions={
          <label className="text-xs text-slate-600">
            Compare with{" "}
            <select
              value={compareWith ?? ""}
              onChange={(e) => onCompare(e.target.value || null)}
              className="rounded border border-slate-300 px-1 py-0.5"
            >
              <option value="">none</option>
              {compareOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {runLabel(o)}
                </option>
              ))}
            </select>
          </label>
        }
      >
        <MetricsTable run={r} other={compareWith && other.data ? other.data : null} />
      </Panel>
      <ByType run={r} />
      <FailedCases run={r} />
      <Panel title="Configuration (frozen for this run)">
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs text-slate-700">
          {JSON.stringify(r.config, null, 2)}
        </pre>
      </Panel>
    </>
  );
}

function Headline({ run }: { run: EvaluationRunDetail }) {
  const d = run.metrics!.deterministic;
  const k = Number(run.config.k ?? 5);
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <MetricCard label={`Recall@${k}`} value={fmt(d[`recall@${k}`])} />
      <MetricCard label="MRR" value={fmt(d.mrr)} />
      <MetricCard label="Correct refusals" value={fmt(d.correct_refusal_rate)} hint="out-of-scope tickets" />
      <MetricCard
        label="Median latency"
        value={run.metrics!.latency_ms.median != null ? `${Math.round(run.metrics!.latency_ms.median)} ms` : "–"}
        hint={`p95 ${run.metrics!.latency_ms.p95 != null ? Math.round(run.metrics!.latency_ms.p95) : "–"} ms`}
      />
    </div>
  );
}

function MetricsTable({ run, other }: { run: EvaluationRunDetail; other: EvaluationRunDetail | null }) {
  const d = run.metrics!.deterministic;
  const o = other?.metrics?.deterministic;
  const keys = Object.keys(d).filter((k) => d[k] !== null || (o && o[k] !== null && o[k] !== undefined));
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase text-slate-500">
        <tr>
          <th scope="col" className="py-1">Metric</th>
          <th scope="col" className="py-1 text-right">{runLabel(run)}</th>
          {other ? <th scope="col" className="py-1 text-right">{runLabel(other)}</th> : null}
          {other ? <th scope="col" className="py-1 text-right">Δ</th> : null}
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {keys.map((key) => {
          const a = d[key];
          const b = o?.[key];
          const delta = a != null && b != null ? a - b : null;
          return (
            <tr key={key}>
              <th scope="row" className="py-1 text-left font-normal">{metricLabel(key)}</th>
              <td className="py-1 text-right font-mono">{fmt(a)}</td>
              {other ? <td className="py-1 text-right font-mono">{fmt(b)}</td> : null}
              {other ? (
                <td className={`py-1 text-right font-mono ${delta && delta > 0 ? "text-emerald-700" : delta && delta < 0 ? "text-red-700" : ""}`}>
                  {delta === null ? "–" : `${delta > 0 ? "+" : ""}${delta.toFixed(3)}`}
                </td>
              ) : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ByType({ run }: { run: EvaluationRunDetail }) {
  const byType = run.metrics!.by_type ?? {};
  const k = Number(run.config.k ?? 5);
  const types = Object.keys(byType).sort();
  if (!types.length) return null;
  return (
    <Panel title="By case type">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr>
            <th scope="col" className="py-1">Type</th>
            <th scope="col" className="py-1 text-right">Hit@{k}</th>
            <th scope="col" className="py-1 text-right">MRR</th>
            <th scope="col" className="py-1 text-right">Correct refusal</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {types.map((t) => (
            <tr key={t}>
              <th scope="row" className="py-1 text-left font-normal">{t.replace(/_/g, " ")}</th>
              <td className="py-1 text-right font-mono">{fmt(byType[t]![`hit_rate@${k}`])}</td>
              <td className="py-1 text-right font-mono">{fmt(byType[t]!.mrr)}</td>
              <td className="py-1 text-right font-mono">{fmt(byType[t]!.correct_refusal_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function FailedCases({ run }: { run: EvaluationRunDetail }) {
  const failed = run.cases.filter((c) => c.passed === false);
  return (
    <Panel title={`Failed cases (${failed.length} of ${run.cases.length})`}>
      {failed.length === 0 ? (
        <p className="text-sm text-slate-600">Every case passed.</p>
      ) : (
        <ul className="space-y-2" aria-label="Failed cases">
          {failed.map((c) => (
            <li key={c.case_id}>
              <details className="rounded-md border border-slate-200 px-3 py-2 text-sm">
                <summary className="cursor-pointer">
                  <span className="font-mono text-xs">{c.case_id}</span> · {String(c.expected.title ?? "")}{" "}
                  <span className="text-xs text-slate-500">({c.case_type.replace(/_/g, " ")})</span>
                </summary>
                {c.error ? <p className="mt-1 text-xs text-red-700">{c.error}</p> : null}
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <div>
                    <p className="text-xs font-medium text-slate-500">Expected</p>
                    <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(c.expected, null, 2)}</pre>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-500">Actual</p>
                    <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(c.actual, null, 2)}</pre>
                  </div>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
