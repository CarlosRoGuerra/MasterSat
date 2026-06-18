import { ReactNode } from 'react';
import { LucideIcon } from 'lucide-react';
import clsx from 'clsx';

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  tone = 'neutral',
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  /** 'neutral' = grey (default), 'success' = green check, 'warning' = amber alert */
  tone?: 'neutral' | 'success' | 'warning';
  className?: string;
}) {
  const iconWrap = {
    neutral: 'bg-slate-100 dark:bg-slate-800',
    success: 'bg-emerald-50 dark:bg-emerald-950/30',
    warning: 'bg-amber-50 dark:bg-amber-950/30',
  }[tone];

  const iconColor = {
    neutral: 'text-slate-400 dark:text-slate-500',
    success: 'text-emerald-500 dark:text-emerald-400',
    warning: 'text-amber-500 dark:text-amber-400',
  }[tone];

  return (
    <div className={clsx('flex flex-col items-center justify-center gap-3 py-16 text-center', className)}>
      {Icon && (
        <div className={clsx('flex h-14 w-14 items-center justify-center rounded-2xl', iconWrap)}>
          <Icon className={clsx('h-6 w-6', iconColor)} strokeWidth={1.5} />
        </div>
      )}
      <div>
        <p className="text-[13px] font-semibold text-slate-700 dark:text-slate-300">{title}</p>
        {description && (
          <p className="mt-1 text-[12px] text-slate-400 dark:text-slate-500">{description}</p>
        )}
      </div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        'animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800',
        className,
      )}
    />
  );
}

export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-px rounded-2xl border border-slate-200 bg-white overflow-hidden dark:border-slate-800 dark:bg-slate-900">
      <div className="flex gap-4 border-b border-slate-100 bg-slate-50/80 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/60">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className="h-3 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={clsx('h-4 flex-1', c === 0 && 'max-w-[140px]')} />
          ))}
        </div>
      ))}
    </div>
  );
}
