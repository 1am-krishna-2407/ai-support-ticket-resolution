import type { ReactNode } from "react";

import { ApiError } from "../services/api";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-8 text-sm text-slate-600">
      <span
        aria-hidden="true"
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600"
      />
      {label}
    </div>
  );
}

function describe(error: unknown): { title: string; message: string; requestId: string | null } {
  if (error instanceof ApiError) {
    if (error.kind === "timeout") {
      return { title: "Request timed out", message: error.message, requestId: error.requestId };
    }
    if (error.isDependencyUnavailable) {
      return {
        title: "Service unavailable",
        message: `${error.message} The backend or one of its dependencies is not responding.`,
        requestId: error.requestId,
      };
    }
    if (error.code === "INVALID_REQUEST") {
      return { title: "Invalid request", message: error.message, requestId: error.requestId };
    }
    return { title: `Error: ${error.code}`, message: error.message, requestId: error.requestId };
  }
  return { title: "Unexpected error", message: String(error), requestId: null };
}

export function ErrorState({ error, action }: { error: unknown; action?: ReactNode }) {
  const { title, message, requestId } = describe(error);
  return (
    <section role="alert" className="rounded-lg border border-red-200 bg-red-50 px-5 py-4 text-sm">
      <h2 className="font-medium text-red-900">{title}</h2>
      <p className="mt-1 text-red-800">{message}</p>
      {requestId ? (
        <p className="mt-2 font-mono text-xs text-red-700">Request ID: {requestId}</p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </section>
  );
}

export function MetricCard({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function Panel({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {actions}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}
