import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { listDocuments, queryKeys, type DocumentQuery } from "../services/endpoints";

export const CATEGORIES = [
  "authentication",
  "api",
  "ci_cd",
  "runners",
  "projects",
  "permissions",
  "integrations",
  "webhooks",
  "troubleshooting",
  "security",
  "configuration",
] as const;

const PAGE_SIZE = 25;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function KnowledgeBasePage() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [allVersions, setAllVersions] = useState(false);
  const [page, setPage] = useState(0);
  const debouncedSearch = useDebounced(search.trim(), 300);

  const q: DocumentQuery = {
    search: debouncedSearch || undefined,
    category: category || undefined,
    all_statuses: allVersions || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: queryKeys.documents(q),
    queryFn: ({ signal }) => listDocuments(q, signal),
    placeholderData: keepPreviousData,
  });

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Documents ingested from GitLab Docs, with their versions and the chunks used for retrieval."
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm font-medium text-slate-800">
          Search
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
            maxLength={200}
            placeholder="Title, ID, or URL"
            className="mt-1 block w-64 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-normal"
          />
        </label>
        <label className="text-sm font-medium text-slate-800">
          Category
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(0);
            }}
            className="mt-1 block rounded-md border border-slate-300 px-2 py-1.5 text-sm font-normal"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 pb-1.5 text-sm text-slate-800">
          <input
            type="checkbox"
            checked={allVersions}
            onChange={(e) => {
              setAllVersions(e.target.checked);
              setPage(0);
            }}
          />
          Include superseded and failed versions
        </label>
        {isFetching && !isPending ? <span className="pb-1.5 text-xs text-slate-500">Updating…</span> : null}
      </div>

      {isPending ? (
        <LoadingState />
      ) : error ? (
        <ErrorState error={error} />
      ) : data.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
          {debouncedSearch || category
            ? "No documents match these filters."
            : "The knowledge base is empty. Run an ingestion job from the Ingestion page."}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <caption className="sr-only">Documents</caption>
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th scope="col" className="px-3 py-2">Title</th>
                  <th scope="col" className="px-3 py-2">Category</th>
                  <th scope="col" className="px-3 py-2">Version</th>
                  <th scope="col" className="px-3 py-2">Status</th>
                  <th scope="col" className="px-3 py-2 text-right">Chunks</th>
                  <th scope="col" className="px-3 py-2">Retrieved</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((d) => (
                  <tr key={`${d.document_id}-${d.version}`}>
                    <td className="px-3 py-2">
                      <Link
                        to={`/knowledge-base/${encodeURIComponent(d.document_id)}`}
                        className="font-medium text-blue-700 underline"
                      >
                        {d.title}
                      </Link>
                      <p className="font-mono text-xs text-slate-500">{d.document_id}</p>
                    </td>
                    <td className="px-3 py-2">{d.category.replace(/_/g, " ")}</td>
                    <td className="px-3 py-2">v{d.version}</td>
                    <td className="px-3 py-2">
                      <StatusBadge value={d.status} />
                      {!d.enabled ? <StatusBadge value="disabled" tone="neutral" /> : null}
                    </td>
                    <td className="px-3 py-2 text-right">{d.chunk_count}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {new Date(d.retrieved_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm">
            <span className="text-slate-600">
              {data.total.toLocaleString()} documents · page {page + 1} of {pages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </nav>
          <p className="mt-3 text-xs text-slate-500">{data.attribution}</p>
        </>
      )}
    </>
  );
}
