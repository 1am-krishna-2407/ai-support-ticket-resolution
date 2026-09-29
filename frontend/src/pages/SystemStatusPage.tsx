import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { getReadiness, queryKeys } from "../services/endpoints";

const LABELS: Record<string, string> = {
  api: "API",
  database: "Database",
  vector_search: "Vector search",
  knowledge_base: "Knowledge base",
  embedding_provider: "Embedding provider",
  llm_provider: "LLM provider",
};

export function SystemStatusPage() {
  const { data, error, isPending, dataUpdatedAt, refetch, isFetching } = useQuery({
    queryKey: queryKeys.readiness,
    queryFn: ({ signal }) => getReadiness(signal),
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader
        title="System Status"
        description="Health of the API and each dependency. Providers are not probed (that would spend credits); their status reflects the most recent request."
        actions={
          <button
            type="button"
            onClick={() => void refetch()}
            disabled={isFetching}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {isFetching ? "Checking…" : "Check now"}
          </button>
        }
      />
      {isPending ? (
        <LoadingState />
      ) : error ? (
        <ErrorState error={error} />
      ) : (
        <>
          <p className="mb-4 flex items-center gap-2 text-sm">
            Overall: <StatusBadge value={data.status} />
            <span className="text-xs text-slate-500">
              checked {new Date(dataUpdatedAt).toLocaleTimeString()}
            </span>
          </p>
          <ul className="grid gap-3 md:grid-cols-2">
            {data.checks.map((c) => (
              <li key={c.name} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold">{LABELS[c.name] ?? c.name}</h2>
                  <StatusBadge value={c.status} />
                </div>
                <p className="mt-1 text-sm text-slate-600">{c.detail}</p>
                {c.latency_ms != null ? (
                  <p className="mt-1 font-mono text-xs text-slate-500">{c.latency_ms} ms</p>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
