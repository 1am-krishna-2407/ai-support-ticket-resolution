import { Link } from "react-router";

import { useAuth } from "../auth/AuthContext";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0]![0]! + parts[parts.length - 1]![0]! : name.slice(0, 2);
  return letters.toUpperCase();
}

function relative(iso: string | null): string {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Bottom of the sidebar: who is signed in, their analysis stats, and account actions. */
export function UserCard() {
  const { me, signOut } = useAuth();
  if (!me) return null;
  const { user, stats } = me;
  const tiles = [
    { label: "Analyses", value: stats.analyses },
    { label: "Drafts", value: stats.drafts },
    { label: "Held back", value: stats.withheld },
    { label: "No match", value: stats.insufficient_evidence },
  ];
  return (
    <section aria-label="Your profile" className="border-t border-slate-200 px-4 py-4">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-800"
        >
          {initials(user.name)}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-900">{user.name}</p>
          <p className="truncate text-xs text-slate-500">{user.email}</p>
        </div>
        <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium capitalize text-slate-700">
          {user.role}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-4 gap-1 text-center">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-md bg-slate-50 px-1 py-1.5">
            <dd className="text-sm font-semibold text-slate-900">{t.value}</dd>
            <dt className="text-[10px] leading-tight text-slate-500">{t.label}</dt>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-[11px] text-slate-500">Last analysis: {relative(stats.last_analysis_at)}</p>
      <div className="mt-3 flex gap-2">
        <Link
          to="/account"
          className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-center text-xs text-slate-700 hover:bg-slate-50"
        >
          Account
        </Link>
        <button
          type="button"
          onClick={() => void signOut()}
          className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
        >
          Sign out
        </button>
      </div>
    </section>
  );
}
