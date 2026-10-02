import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { LoadingState } from "../components/States";
import { ApiError } from "../services/api";
import { getAuthConfig, queryKeys, register } from "../services/endpoints";
import { safeNext } from "./LoginPage";

const INPUT = "w-full rounded-md border border-slate-300 px-3 py-2 text-sm";

export function RegisterPage() {
  const { me, checking } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const next = safeNext(params.get("next"));
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const config = useQuery({
    queryKey: queryKeys.authConfig,
    queryFn: ({ signal }) => getAuthConfig(signal),
    staleTime: 300_000,
  });
  const mutation = useMutation({
    mutationFn: () => register({ email: email.trim(), name: name.trim(), password }),
    onSuccess: (result) => {
      if (result.status === "active" && result.me) {
        queryClient.setQueryData(queryKeys.me, result.me);
        void navigate(next, { replace: true });
      }
    },
  });

  if (!checking && me) return <Navigate to={next} replace />;
  const minLength = config.data?.password_min_length ?? 12;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setMismatch(password !== repeat);
    if (password === repeat) mutation.mutate();
  };

  const error = mutation.error;
  const message =
    error instanceof ApiError
      ? error.code === "RATE_LIMITED"
        ? "Too many attempts. Wait a minute and try again."
        : error.message
      : error
        ? "Could not create the account. Please try again."
        : null;

  let body;
  if (config.isPending) {
    body = <LoadingState />;
  } else if (config.data?.registration === "closed") {
    body = (
      <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-6 text-sm shadow-sm">
        <h1 className="text-base font-semibold text-slate-900">Sign-up is closed</h1>
        <p className="text-slate-600">Ask an administrator to create an account for you.</p>
        <Link to="/login" className="text-blue-700 underline">
          Back to sign in
        </Link>
      </div>
    );
  } else if (mutation.data?.status === "pending") {
    body = (
      <div role="status" className="space-y-3 rounded-lg border border-slate-200 bg-white p-6 text-sm shadow-sm">
        <h1 className="text-base font-semibold text-slate-900">Account requested</h1>
        <p className="text-slate-600">
          An administrator needs to approve your account. You can sign in once it is approved.
        </p>
        <Link to="/login" className="text-blue-700 underline">
          Back to sign in
        </Link>
      </div>
    );
  } else {
    body = (
      <form
        onSubmit={onSubmit}
        aria-label="Create an account"
        className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-base font-semibold text-slate-900">Create an account</h1>
        {config.data?.registration === "approval" ? (
          <p className="text-xs text-slate-500">New accounts are activated by an administrator.</p>
        ) : null}
        <div>
          <label htmlFor="reg-name" className="mb-1 block text-sm font-medium text-slate-800">
            Name
          </label>
          <input id="reg-name" required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={INPUT} />
        </div>
        <div>
          <label htmlFor="reg-email" className="mb-1 block text-sm font-medium text-slate-800">
            Email
          </label>
          <input
            id="reg-email"
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={INPUT}
          />
        </div>
        <div>
          <label htmlFor="reg-password" className="mb-1 block text-sm font-medium text-slate-800">
            Password <span className="font-normal text-slate-500">(at least {minLength} characters)</span>
          </label>
          <input
            id="reg-password"
            type="password"
            required
            minLength={minLength}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={INPUT}
          />
        </div>
        <div>
          <label htmlFor="reg-repeat" className="mb-1 block text-sm font-medium text-slate-800">
            Repeat password
          </label>
          <input
            id="reg-repeat"
            type="password"
            required
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            className={INPUT}
          />
        </div>
        {mismatch ? (
          <p role="alert" className="text-sm text-red-700">
            The passwords do not match.
          </p>
        ) : message ? (
          <p role="alert" className="text-sm text-red-700">
            {message}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full rounded-md bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-60"
        >
          {mutation.isPending ? "Creating account…" : "Create account"}
        </button>
        <p className="text-center text-sm text-slate-600">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-blue-700 underline">
            Sign in
          </Link>
        </p>
      </form>
    );
  }

  return (
    <main id="main" className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <p className="text-lg font-semibold tracking-tight text-slate-900">Support Resolution</p>
          <p className="text-sm text-slate-500">AI-assisted ticket drafts</p>
        </div>
        {body}
      </div>
    </main>
  );
}
