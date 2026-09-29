import type { Citation } from "../services/schemas";

export const ATTRIBUTION_NOTE =
  "Content from GitLab Docs (docs.gitlab.com), licensed under CC BY-SA 4.0. Not affiliated with GitLab Inc.";

export function SourceCard({ source }: { source: Citation }) {
  return (
    <article className="rounded-md border border-slate-200 px-3 py-2.5" aria-labelledby={`src-${source.source_id}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`src-${source.source_id}`} className="text-sm font-medium">
          <span className="mr-2 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
            {source.source_id}
          </span>
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-700 underline decoration-blue-300 underline-offset-2 hover:decoration-blue-700"
          >
            {source.title}
          </a>
        </h3>
        <span className="font-mono text-xs text-slate-500">
          similarity {source.semantic_score.toFixed(3)}
        </span>
      </div>
      {source.section ? <p className="mt-0.5 text-xs text-slate-500">{source.section}</p> : null}
      <p className="mt-1.5 line-clamp-3 whitespace-pre-line text-xs text-slate-700">{source.excerpt}</p>
    </article>
  );
}
