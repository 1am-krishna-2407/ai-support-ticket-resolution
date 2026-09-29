import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, MetricCard, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { getIngestionStatus, getReadiness, queryKeys } from "../services/endpoints";

export function DashboardPage() {
  const readiness = useQuery({
    queryKey: queryKeys.readiness,
    queryFn: ({ signal }) => getReadiness(signal),
  });
  const ingestion = useQuery({
    queryKey: queryKeys.ingestionStatus,
    queryFn: ({ signal }) => getIngestionStatus(signal),
  });

  const kb = readiness.data?.knowledge_base;
  const job = ingestion.data?.latest;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Knowledge-base coverage, system health, ingestion activity, and evaluation results. Every figure comes from the backend; nothing here is estimated."
      />
      {readiness.isPending ? (
        <LoadingState />
      ) : readiness.isError ? (
        <ErrorState error={readiness.error} />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <MetricCard label="Active documents" value={kb?.documents ?? "–"} />
            <MetricCard label="Chunks" value={kb?.chunks ?? "–"} />
            <MetricCard
              label="Embedded chunks"
              value={kb?.embedded_chunks ?? "–"}
              hint={
                kb && kb.chunks
                  ? `${Math.round((kb.embedded_chunks / kb.chunks) * 100)}% searchable by meaning`
                  : undefined
              }
            />
            <MetricCard label="System" value={<StatusBadge value={readiness.data.status} />} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel
              title="Last ingestion"
              actions={
                <Link to="/ingestion" className="text-xs font-medium text-blue-700 underline">
                  Details
                </Link>
              }
            >
              {ingestion.isPending ? (
                <LoadingState />
              ) : ingestion.isError ? (
                <ErrorState error={ingestion.error} />
              ) : job ? (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                  <dt className="text-slate-500">Status</dt>
                  <dd>
                    <StatusBadge value={job.status} />
                  </dd>
                  <dt className="text-slate-500">Processed</dt>
                  <dd>
                    {job.processed_documents} / {job.total_documents} ({job.changed_documents} changed,{" "}
                    {job.failed_documents} failed)
                  </dd>
                  <dt className="text-slate-500">When</dt>
                  <dd>{new Date(job.created_at).toLocaleString()}</dd>
                </dl>
              ) : (
                <p className="text-sm text-slate-600">No ingestion job has run yet.</p>
              )}
            </Panel>

            <Panel
              title="Dependencies"
              actions={
                <Link to="/status" className="text-xs font-medium text-blue-700 underline">
                  Details
                </Link>
              }
            >
              <ul className="space-y-1 text-sm">
                {readiness.data.checks.map((c) => (
                  <li key={c.name} className="flex items-center justify-between">
                    <span>{c.name.replace(/_/g, " ")}</span>
                    <StatusBadge value={c.status} />
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <Panel title="Evaluation">
            <p className="text-sm text-slate-600">No evaluation results available yet.</p>
          </Panel>
        </div>
      )}
    </>
  );
}
