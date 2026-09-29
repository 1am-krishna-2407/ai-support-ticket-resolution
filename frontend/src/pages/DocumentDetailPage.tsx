import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { getDocument, listChunks, queryKeys } from "../services/endpoints";

const CHUNK_PAGE = 10;

export function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [offset, setOffset] = useState(0);

  const doc = useQuery({
    queryKey: queryKeys.document(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
  });
  const chunks = useQuery({
    queryKey: queryKeys.chunks(documentId, offset, CHUNK_PAGE, version),
    queryFn: ({ signal }) => listChunks(documentId, offset, CHUNK_PAGE, version, signal),
    enabled: doc.isSuccess,
    placeholderData: keepPreviousData,
  });

  if (doc.isPending) return <LoadingState />;
  if (doc.isError) {
    return (
      <ErrorState
        error={doc.error}
        action={
          <Link to="/knowledge-base" className="text-sm font-medium text-blue-700 underline">
            Back to the knowledge base
          </Link>
        }
      />
    );
  }
  const d = doc.data;

  return (
    <>
      <PageHeader
        title={d.title}
        description={`${d.document_id} · ${d.category.replace(/_/g, " ")} · ${d.license}`}
        actions={
          <Link to="/knowledge-base" className="text-sm font-medium text-blue-700 underline">
            All documents
          </Link>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="space-y-4">
          <Panel title="Current version">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-slate-500">Version</dt>
              <dd>
                v{d.version} <StatusBadge value={d.status} />
              </dd>
              <dt className="text-slate-500">Source</dt>
              <dd>
                <a href={d.canonical_url} target="_blank" rel="noopener noreferrer" className="break-all text-blue-700 underline">
                  {d.canonical_url}
                </a>
              </dd>
              <dt className="text-slate-500">Chunks</dt>
              <dd>{d.chunk_count}</dd>
              <dt className="text-slate-500">Embeddings</dt>
              <dd className="font-mono text-xs">{d.embedding_model ?? "none"}</dd>
              <dt className="text-slate-500">Retrieved</dt>
              <dd>{new Date(d.retrieved_at).toLocaleString()}</dd>
              <dt className="text-slate-500">Content hash</dt>
              <dd className="truncate font-mono text-xs" title={d.content_hash}>
                {d.content_hash.slice(0, 16)}…
              </dd>
            </dl>
          </Panel>
          <Panel title={`Version history (${d.versions.length})`}>
            <ul className="space-y-2 text-sm">
              {d.versions.map((v) => (
                <li key={v.version} className="flex flex-wrap items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setVersion(v.status === "active" ? undefined : v.version);
                      setOffset(0);
                    }}
                    aria-pressed={(version ?? d.version) === v.version}
                    className="font-medium text-blue-700 underline"
                  >
                    v{v.version}
                  </button>
                  <StatusBadge value={v.status} />
                  <span className="text-xs text-slate-500">
                    {v.chunk_count} chunks · {new Date(v.retrieved_at).toLocaleDateString()}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
          <p className="text-xs text-slate-500">{d.attribution}</p>
        </div>

        <Panel title={`Chunks${version ? ` (version ${version})` : ""}`}>
          {chunks.isPending ? (
            <LoadingState />
          ) : chunks.isError ? (
            <ErrorState error={chunks.error} />
          ) : chunks.data.items.length === 0 ? (
            <p className="text-sm text-slate-600">This version has no chunks.</p>
          ) : (
            <>
              <ol className="space-y-3">
                {chunks.data.items.map((c) => (
                  <li key={c.chunk_id} className="rounded-md border border-slate-200 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                      <span className="font-medium text-slate-800">
                        #{c.chunk_index} · {c.heading_path}
                      </span>
                      <span>
                        {c.token_count} tokens
                        {c.has_code ? " · code" : ""}
                        {c.is_split_oversize ? " · split" : ""}
                        {c.embedding_model ? "" : " · not embedded"}
                      </span>
                    </div>
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-slate-800">
                      {c.content}
                    </pre>
                  </li>
                ))}
              </ol>
              <nav aria-label="Chunk pages" className="mt-3 flex items-center justify-between text-sm">
                <span className="text-slate-600">
                  {offset + 1}–{Math.min(offset + CHUNK_PAGE, chunks.data.total)} of {chunks.data.total}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset((o) => Math.max(0, o - CHUNK_PAGE))}
                    className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={offset + CHUNK_PAGE >= chunks.data.total}
                    onClick={() => setOffset((o) => o + CHUNK_PAGE)}
                    className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </nav>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
