type Tone = "good" | "warn" | "bad" | "neutral" | "info";

const TONES: Record<Tone, string> = {
  good: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  warn: "bg-amber-50 text-amber-800 ring-amber-600/20",
  bad: "bg-red-50 text-red-800 ring-red-600/20",
  neutral: "bg-slate-100 text-slate-700 ring-slate-500/20",
  info: "bg-blue-50 text-blue-800 ring-blue-600/20",
};

const KNOWN: Record<string, Tone> = {
  ok: "good",
  active: "good",
  completed: "good",
  draft: "good",
  grounded: "good",
  low: "neutral",
  degraded: "warn",
  medium: "warn",
  queued: "info",
  running: "info",
  validating: "info",
  indexing: "info",
  publishing: "info",
  staged: "info",
  superseded: "neutral",
  not_configured: "neutral",
  insufficient_evidence: "warn",
  withheld: "warn",
  high: "bad",
  critical: "bad",
  failed: "bad",
  unavailable: "bad",
};

export function toneFor(value: string): Tone {
  return KNOWN[value] ?? "neutral";
}

export function StatusBadge({ value, label, tone }: { value: string; label?: string; tone?: Tone }) {
  const t = tone ?? toneFor(value);
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[t]}`}
    >
      {label ?? value.replace(/_/g, " ")}
    </span>
  );
}
