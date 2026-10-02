import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { ErrorState, LoadingState } from "../components/States";
import { useAuth } from "./AuthContext";

/** Renders children for a signed-in user; otherwise sends them to /login and back. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, checking, error } = useAuth();
  const location = useLocation();
  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LoadingState label="Checking your session…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="mx-auto max-w-lg px-6 py-16">
        <ErrorState error={error} />
      </div>
    );
  }
  if (!me) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  return <>{children}</>;
}

/** Admin-only sections: agents see an explanation instead of a broken page. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  if (!isAdmin) {
    return (
      <section role="alert" className="rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm">
        <h1 className="font-medium text-slate-900">Admins only</h1>
        <p className="mt-1 text-slate-600">Ask an admin if you need access to this page.</p>
      </section>
    );
  }
  return <>{children}</>;
}
