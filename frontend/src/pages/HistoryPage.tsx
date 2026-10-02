import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { HISTORY_PAGE_SIZE, listHistory, queryKeys } from "../services/endpoints";
import { RESOLUTION_STATUS_TEXT } from "./TicketAnalysisPage";

function formatSeconds(ms: number | null): string {
  return ms == null ? "–" : `${Math.round(ms / 1000)} s`;
}

export function HistoryPage() {
  const { isAdmin } = useAuth();
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: queryKeys.history(scope, offset),
    queryFn: ({ signal }) => listHistory(scope, offset, signal),
    placeholderData: keepPreviousData,
  });

  const changeScope = (next: "mine" | "all") => {
    setScope(next);
    setOffset(0);
  };

  return (
    <>
      <PageHeader
        title="History"
        description={
          scope === "all"
            ? "Every user's saved ticket analyses, newest first."
            : "Your saved ticket analyses, newest first. Only you (and admins) can see them."
        }
        actions={
          isAdmin ? (
            <div role="group" aria-label="Whose analyses" className="flex rounded-md border border-slate-300 text-sm">
              {(["mine", "all"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={scope === s}
                  onClick={() => changeScope(s)}
                  className={`px-3 py-1.5 ${scope === s ? "bg-blue-50 font-medium text-blue-800" : "text-slate-700"}`}
                >
                  {s === "mine" ? "Mine" : "All users"}
                </button>
              ))}
            </div>
          ) : null
        }
      />
      {query.isPending ? (
        <LoadingState />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : query.data.total === 0 ? (
        <EmptyState title="No saved analyses yet">
          Analyses run on the Ticket Analysis page are saved here automatically.{" "}
          <Link to="/tickets" className="text-blue-700 underline">
            Analyse a ticket
          </Link>
        </EmptyState>
      ) : (
        <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">Ticket</th>
                <th className="px-4 py-2 font-medium">Outcome</th>
                <th className="px-4 py-2 font-medium">Category</th>
                <th className="px-4 py-2 font-medium">Priority</th>
                {scope === "all" ? <th className="px-4 py-2 font-medium">User</th> : null}
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-4 py-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {query.data.items.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50">
                  <td className="max-w-md px-4 py-2">
                    <Link to={`/history/${item.id}`} className="font-medium text-blue-700 hover:underline">
                      {item.title}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge value={item.status} label={RESOLUTION_STATUS_TEXT[item.status]} />
                  </td>
                  <td className="px-4 py-2 capitalize text-slate-700">{item.category?.replace(/_/g, " ") ?? "–"}</td>
                  <td className="px-4 py-2">{item.priority ? <StatusBadge value={item.priority} /> : "–"}</td>
                  {scope === "all" ? <td className="px-4 py-2 text-slate-700">{item.user_email}</td> : null}
                  <td className="px-4 py-2 text-slate-600">{formatSeconds(item.latency_ms)}</td>
                  <td className="whitespace-nowrap px-4 py-2 text-slate-600">
                    {new Date(item.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2 text-xs text-slate-600">
            <span>
              {offset + 1}–{Math.min(offset + HISTORY_PAGE_SIZE, query.data.total)} of {query.data.total}
            </span>
            <span className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - HISTORY_PAGE_SIZE))}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + HISTORY_PAGE_SIZE >= query.data.total}
                onClick={() => setOffset(offset + HISTORY_PAGE_SIZE)}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </span>
          </div>
        </section>
      )}
    </>
  );
}
