import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../services/api";
import { getHealth, systemKeys } from "../services/system";

/** Live API reachability, polled from GET /health. Never shows a status it hasn't observed. */
export function ApiStatusIndicator() {
  const { status, error, isFetching } = useQuery({
    queryKey: systemKeys.health,
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 30_000,
    retry: false,
  });

  let label: string;
  let dot: string;
  let detail: string | undefined;
  if (status === "pending") {
    label = "Checking API…";
    dot = "bg-slate-400";
  } else if (status === "success") {
    label = "API online";
    dot = "bg-emerald-500";
  } else {
    label = "API unreachable";
    dot = "bg-red-500";
    detail =
      error instanceof ApiError
        ? `${error.message}${error.requestId ? ` (request ${error.requestId})` : ""}`
        : "Unknown error";
  }

  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 text-sm" title={detail}>
      <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${dot}`} />
      <span className="text-slate-700">{label}</span>
      {isFetching && status !== "pending" ? <span className="sr-only">(refreshing)</span> : null}
    </div>
  );
}
