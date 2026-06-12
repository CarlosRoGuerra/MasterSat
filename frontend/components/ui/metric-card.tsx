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
        <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 dark:text-slate-500">{label}</p>
        {icon && <span className="shrink-0 text-slate-400 dark:text-slate-500">{icon}</span>}
      </div>
      <p className="mt-2 text-[28px] font-medium leading-none tabular-nums text-slate-900 dark:text-white">{value}</p>
      {sub && <p className="mt-1.5 text-[12px] text-slate-400 dark:text-slate-500">{sub}</p>}
    </div>
  );
}
