import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../services/api";
import { getAuthConfig, login, queryKeys } from "../services/endpoints";

/** Only same-app paths: never redirect to another site after sign-in. */
export function safeNext(next: string | null): string {
  if (
    !next ||
    !next.startsWith("/") ||
    next.startsWith("//") ||
    next.startsWith("/login") ||
    next.startsWith("/register")
  ) {
    return "/";
  }
  return next;
}

export function LoginPage() {
  const { me, checking } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const next = safeNext(params.get("next"));
  const config = useQuery({
    queryKey: queryKeys.authConfig,
    queryFn: ({ signal }) => getAuthConfig(signal),
    staleTime: 300_000,
  });
  const canRegister = config.data && config.data.registration !== "closed";

  const mutation = useMutation({
    mutationFn: () => login(email.trim(), password),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.me, data);
      void navigate(next, { replace: true });
    },
  });

  if (!checking && me) return <Navigate to={next} replace />;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (email.trim() && password) mutation.mutate();
  };

  const error = mutation.error;
  const message =
    error instanceof ApiError
      ? error.code === "RATE_LIMITED"
        ? "Too many sign-in attempts. Wait a minute and try again."
        : error.status === 401
          ? "The email or password is incorrect, or the account is disabled."
          : error.message
      : error
        ? "Sign-in failed. Please try again."
        : null;

  return (
    <main id="main" className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <p className="text-lg font-semibold tracking-tight text-slate-900">Support Resolution</p>
          <p className="text-sm text-slate-500">AI-assisted ticket drafts</p>
        </div>
        <form
          onSubmit={onSubmit}
          aria-label="Sign in"
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <h1 className="text-base font-semibold text-slate-900">Sign in</h1>
          <div>
            <label htmlFor="login-email" className="mb-1 block text-sm font-medium text-slate-800">
              Email
            </label>
            <input
              id="login-email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="mb-1 block text-sm font-medium text-slate-800">
              Password
            </label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          {message ? (
            <p role="alert" className="text-sm text-red-700">
              {message}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full rounded-md bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-60"
          >
            {mutation.isPending ? "Signing in…" : "Sign in"}
          </button>
          {canRegister ? (
            <p className="text-center text-sm text-slate-600">
              New here?{" "}
              <Link to={`/register${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} className="font-medium text-blue-700 underline">
                Create an account
              </Link>
            </p>
          ) : config.data ? (
            <p className="text-xs text-slate-500">Accounts are created by an administrator.</p>
          ) : null}
        </form>
      </div>
    </main>
  );
}
