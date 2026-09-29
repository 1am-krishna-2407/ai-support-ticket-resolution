import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, MetricCard, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { ApiError } from "../services/api";
import { getIngestionStatus, queryKeys, startIngestion } from "../services/endpoints";
import type { IngestionJob } from "../services/schemas";

const POLL_MS = 2000;

export function IngestionPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: queryKeys.ingestionStatus,
    queryFn: ({ signal }) => getIngestionStatus(signal),
    refetchInterval: (query) => (query.state.data?.running ? POLL_MS : false),
  });
  // Held in memory only: never persisted, and sent only with the trigger request.
  const [token, setToken] = useState("");
  const [force, setForce] = useState(false);
  const trigger = useMutation({
    mutationFn: () => startIngestion(token, force),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.ingestionStatus }),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (token) trigger.mutate();
  }

  const running = status.data?.running ?? false;

  return (
    <>
      <PageHeader
        title="Ingestion"
        description="Fetch the curated documentation, detect changes, re-embed what changed, and publish a new knowledge-base version. Only one job runs at a time."
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          {status.isPending ? (
            <LoadingState />
          ) : status.isError ? (
            <ErrorState error={status.error} />
          ) : status.data.latest ? (
            <JobPanel job={status.data.latest} running={running} />
          ) : (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
              No ingestion job has run yet.
            </p>
          )}
        </div>

        <Panel title="Start a job">
          <form onSubmit={onSubmit} className="space-y-3" aria-label="Start ingestion">
            <label className="block text-sm font-medium text-slate-800">
              Admin token
              <input
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm font-normal"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-800">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              Re-process unchanged documents (stored embeddings are reused)
            </label>
            <button
              type="submit"
              disabled={!token || running || trigger.isPending}
              className="w-full rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {trigger.isPending ? "Starting…" : running ? "A job is running" : "Start ingestion"}
            </button>
            <p className="text-xs text-slate-500">
              {!token
                ? "Starting a job requires the admin token configured on the server (ADMIN_TOKEN)."
                : running
                  ? "Wait for the current job to finish."
                  : "The job runs in the background; this page updates while it runs."}
            </p>
            {trigger.isError ? <TriggerError error={trigger.error} /> : null}
            {trigger.isSuccess ? (
              <p role="status" className="text-xs text-emerald-800">
                Job {trigger.data.id.slice(0, 8)} queued.
              </p>
            ) : null}
          </form>
        </Panel>
      </div>
    </>
  );
}

function TriggerError({ error }: { error: unknown }) {
  if (error instanceof ApiError && (error.code === "UNAUTHORIZED" || error.code === "FORBIDDEN")) {
    return (
      <p role="alert" className="text-xs text-red-700">
        The admin token was rejected.
      </p>
    );
  }
  return <ErrorState error={error} />;
}

function JobPanel({ job, running }: { job: IngestionJob; running: boolean }) {
  const pct = job.total_documents ? Math.round((job.processed_documents / job.total_documents) * 100) : 0;
  return (
    <Panel
      title={`${running ? "Current" : "Last"} job`}
      actions={<StatusBadge value={job.status} />}
    >
      <p className="font-mono text-xs text-slate-500">
        {job.id} · triggered by {job.trigger} · created {new Date(job.created_at).toLocaleString()}
        {job.finished_at ? ` · finished ${new Date(job.finished_at).toLocaleString()}` : ""}
      </p>
      <div
        className="mt-3 h-2 overflow-hidden rounded bg-slate-200"
        role="progressbar"
        aria-label="Documents processed"
        aria-valuemin={0}
        aria-valuemax={job.total_documents}
        aria-valuenow={job.processed_documents}
      >
        <div className="h-full bg-blue-600 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Processed" value={`${job.processed_documents}/${job.total_documents}`} />
        <MetricCard label="Changed" value={job.changed_documents} />
        <MetricCard label="Failed" value={job.failed_documents} />
        <MetricCard label="Chunks" value={job.total_chunks} />
        <MetricCard label="Embedding calls" value={job.embedding_calls} />
        <MetricCard label="Reused embeddings" value={job.reused_embeddings} />
      </div>
      {job.error_summary.length ? (
        <div className="mt-3">
          <p className="text-sm font-medium text-slate-800">Errors ({job.error_summary.length})</p>
          <ul className="mt-1 max-h-64 space-y-1 overflow-auto text-xs">
            {job.error_summary.map((e, i) => (
              <li key={i} className="rounded bg-red-50 px-2 py-1 text-red-900">
                {e.document_id ? <span className="font-mono">{String(e.document_id)}: </span> : null}
                {String(e.message ?? e.error ?? "error")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
