interface ScoreBarProps {
  label: string;
  value: number | null;
  /** Draw a marker at this value (e.g. the relevance threshold). */
  threshold?: number;
  max?: number;
}

export function ScoreBar({ label, value, threshold, max = 1 }: ScoreBarProps) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 shrink-0 text-slate-600">{label}</span>
      <div
        className="relative h-2 flex-1 overflow-hidden rounded bg-slate-200"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value ?? undefined}
      >
        <div className="h-full bg-blue-600" style={{ width: `${pct}%` }} />
        {threshold !== undefined ? (
          <div
            className="absolute top-0 h-full w-0.5 bg-slate-900"
            style={{ left: `${Math.min(100, (threshold / max) * 100)}%` }}
            title={`threshold ${threshold}`}
          />
        ) : null}
      </div>
      <span className="w-14 shrink-0 text-right font-mono text-slate-800">
        {value === null ? "n/a" : value.toFixed(3)}
      </span>
    </div>
  );
}
