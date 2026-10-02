import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";
import { MetricCard, Panel } from "../components/States";
import { ApiError } from "../services/api";
import { changePassword } from "../services/endpoints";

export function AccountPage() {
  const { me } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const mutation = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setRepeat("");
    },
  });
  if (!me) return null;
  const { user, stats } = me;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setMismatch(next !== repeat);
    if (next === repeat) mutation.mutate();
  };

  return (
    <>
      <PageHeader title="Account" description={`${user.name} · ${user.email} · ${user.role}`} />
      <div className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Analyses" value={stats.analyses} />
          <MetricCard label="Drafts" value={stats.drafts} hint="Grounded, validated drafts" />
          <MetricCard label="Held back" value={stats.withheld} hint="Failed validation" />
          <MetricCard label="No match" value={stats.insufficient_evidence} hint="Docs did not cover it" />
          <MetricCard label="Unavailable" value={stats.unavailable} hint="Provider problems" />
        </div>
        <Panel title="Change password">
          <form onSubmit={onSubmit} aria-label="Change password" className="grid max-w-md gap-3">
            {[
              { id: "pw-current", label: "Current password", value: current, set: setCurrent, auto: "current-password" },
              { id: "pw-new", label: "New password (at least 12 characters)", value: next, set: setNext, auto: "new-password" },
              { id: "pw-repeat", label: "Repeat new password", value: repeat, set: setRepeat, auto: "new-password" },
            ].map((f) => (
              <label key={f.id} htmlFor={f.id} className="text-sm text-slate-800">
                {f.label}
                <input
                  id={f.id}
                  type="password"
                  required
                  autoComplete={f.auto}
                  value={f.value}
                  onChange={(e) => f.set(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
              </label>
            ))}
            {mismatch ? <p role="alert" className="text-sm text-red-700">The new passwords do not match.</p> : null}
            {mutation.isError ? (
              <p role="alert" className="text-sm text-red-700">
                {mutation.error instanceof ApiError ? mutation.error.message : "Could not change the password."}
              </p>
            ) : null}
            {mutation.isSuccess ? (
              <p className="text-sm text-green-700">Password changed. Your other browsers were signed out.</p>
            ) : null}
            <button
              type="submit"
              disabled={mutation.isPending}
              className="justify-self-start rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-60"
            >
              {mutation.isPending ? "Saving…" : "Change password"}
            </button>
          </form>
        </Panel>
      </div>
    </>
  );
}
