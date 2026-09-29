import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
}

export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <section className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <h2 className="text-base font-medium text-slate-800">{title}</h2>
      {children ? <div className="mx-auto mt-2 max-w-xl text-sm text-slate-600">{children}</div> : null}
    </section>
  );
}
