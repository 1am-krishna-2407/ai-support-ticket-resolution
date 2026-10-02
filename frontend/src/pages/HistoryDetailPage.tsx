import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, Panel } from "../components/States";
import { deleteHistoryItem, getHistoryItem, queryKeys } from "../services/endpoints";
import { AnalysisResult } from "./TicketAnalysisPage";

export function HistoryDetailPage() {
  const { analysisId = "" } = useParams();
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.historyItem(analysisId),
    queryFn: ({ signal }) => getHistoryItem(analysisId, signal),
  });
  const remove = useMutation({
    mutationFn: () => deleteHistoryItem(analysisId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      void refresh();
      void navigate("/history", { replace: true });
    },
  });

  const back = (
    <Link to="/history" className="text-sm text-blue-700 underline">
      Back to history
    </Link>
  );
  if (query.isPending) return <LoadingState />;
  if (query.isError) {
    return (
      <>
        <PageHeader title="Saved analysis" actions={back} />
        <ErrorState error={query.error} />
      </>
    );
  }

  const item = query.data;
  const environment = Object.entries(item.environment ?? {});
  const someoneElse = me && item.user_email !== me.user.email;
  return (
    <>
      <PageHeader
        title={item.title}
        description={`Analysed ${new Date(item.created_at).toLocaleString()}${someoneElse ? ` by ${item.user_email}` : ""}.`}
        actions={
          <>
            {back}
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm("Delete this saved analysis? This cannot be undone.")) remove.mutate();
              }}
              className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              Delete
            </button>
          </>
        }
      />
      {remove.isError ? <ErrorState error={remove.error} /> : null}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,22rem)_1fr]">
        <Panel title="Ticket">
          <p className="whitespace-pre-line text-sm text-slate-800">{item.description}</p>
          {environment.length ? (
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {environment.map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-slate-500">{key.replace(/_/g, " ")}</dt>
                  <dd className="text-slate-800">{String(value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </Panel>
        <AnalysisResult result={item.result} />
      </div>
    </>
  );
}
