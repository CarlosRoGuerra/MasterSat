import { ReactNode } from 'react';
import clsx from 'clsx';

type Tone = 'default' | 'brand' | 'success' | 'warning' | 'danger';

const toneConfig: Record<Tone, { card: string; icon: string; value: string }> = {
  default:  { card: 'bg-white border-slate-200 dark:bg-slate-900 dark:border-slate-800', icon: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400', value: 'text-slate-900 dark:text-white' },
  brand:    { card: 'bg-brand-50 border-brand-200 dark:bg-brand-950/40 dark:border-brand-900/60', icon: 'bg-brand-100 text-brand-600 dark:bg-brand-900/60 dark:text-brand-300', value: 'text-brand-700 dark:text-brand-200' },
  success:  { card: 'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-900/60', icon: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-400', value: 'text-emerald-700 dark:text-emerald-300' },
  warning:  { card: 'bg-amber-50 border-amber-200 dark:bg-amber-950/40 dark:border-amber-900/60', icon: 'bg-amber-100 text-amber-600 dark:bg-amber-900/60 dark:text-amber-400', value: 'text-amber-700 dark:text-amber-300' },
  danger:   { card: 'bg-rose-50 border-rose-200 dark:bg-rose-950/40 dark:border-rose-900/60', icon: 'bg-rose-100 text-rose-600 dark:bg-rose-900/60 dark:text-rose-400', value: 'text-rose-700 dark:text-rose-300' },
};

export function StatCard({
  label,
  value,
  hint,
  tone = 'default',
  icon,
  delta,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: Tone;
  icon?: ReactNode;
  /** Optional month-over-month delta */
  delta?: { value: number; pct?: number };
}) {
  const cfg = toneConfig[tone];

  const deltaColor = !delta ? '' :
    delta.value > 0 ? 'text-emerald-600 dark:text-emerald-400' :
    delta.value < 0 ? 'text-rose-500 dark:text-rose-400' :
    'text-slate-400 dark:text-slate-500';

  const deltaPrefix = !delta ? '' : delta.value > 0 ? '↑' : delta.value < 0 ? '↓' : '=';

  return (
    <div className={clsx('rounded-2xl border p-5 shadow-card', cfg.card)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500 dark:text-slate-400">
            {label}
          </p>
          <p className={clsx('mt-2 text-[28px] font-medium leading-none tabular-nums', cfg.value)}>
            {value}
          </p>
          <div className="mt-1.5 flex items-center gap-2">
            {hint && (
              <p className="text-[12px] text-slate-500 dark:text-slate-400">{hint}</p>
            )}
            {delta && delta.value !== 0 && (
              <span className={clsx('text-[11px] font-semibold tabular-nums', deltaColor)}>
                {deltaPrefix} {Math.abs(delta.value)}
                {delta.pct !== undefined && delta.pct !== 0 && (
                  <span className="ml-0.5 font-normal opacity-70">({Math.abs(delta.pct)}%)</span>
                )}
                <span className="ml-0.5 font-normal opacity-60">vs mês ant.</span>
              </span>
            )}
          </div>
        </div>
        {icon && (
          <div className={clsx('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', cfg.icon)}>
            {icon}
          </div>
        )}
      </div>
    </div>
  );
}
