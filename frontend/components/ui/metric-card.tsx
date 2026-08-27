import { ReactNode } from 'react';

export function MetricCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-700/50 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-slate-500 dark:text-slate-400">{label}</p>
        {icon && <span className="shrink-0 text-slate-500 dark:text-slate-400">{icon}</span>}
      </div>
      <p className="mt-2 text-stat font-semibold leading-none tabular-nums text-slate-900 dark:text-white">{value}</p>
      {sub && <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
    </div>
  );
}
