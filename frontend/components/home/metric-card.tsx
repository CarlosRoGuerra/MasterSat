'use client';

import { ReactNode } from 'react';
import clsx from 'clsx';
import { ArrowDownRight, ArrowUpRight, Fuel, Target, Timer, Truck } from 'lucide-react';
import { useCountUp } from './use-count-up';
import { useReveal } from './use-reveal';
import { formatMetricValue, type HomeMetric, type MetricIcon } from '@/lib/mock-logistics';

const ICONS: Record<MetricIcon, ReactNode> = {
  truck: <Truck className="h-5 w-5" aria-hidden="true" />,
  target: <Target className="h-5 w-5" aria-hidden="true" />,
  timer: <Timer className="h-5 w-5" aria-hidden="true" />,
  fuel: <Fuel className="h-5 w-5" aria-hidden="true" />,
};

export function MetricCard({ metric, delayMs = 0 }: { metric: HomeMetric; delayMs?: number }) {
  const animated = useCountUp(metric.value);
  const { ref, visible } = useReveal<HTMLDivElement>();

  const direction = metric.changePct >= 0 ? 'up' : 'down';
  const isPositive = direction === metric.goodDirection;

  return (
    <div
      ref={ref}
      className={clsx(
        'rounded-2xl border border-[color:var(--fh-border)] bg-[color:var(--fh-surface)] p-6 backdrop-blur-xl transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)] hover:border-[color:var(--fh-accent)]/40 hover:bg-[color:var(--fh-surface-strong)]',
        visible ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0',
      )}
      style={{ transitionDelay: visible ? `${delayMs}ms` : '0ms' }}
    >
      <div className="flex items-center justify-between">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[color:var(--fh-accent-soft)] text-[color:var(--fh-accent)]">
          {ICONS[metric.icon]}
        </span>
        <span
          className={clsx(
            'inline-flex items-center gap-1 rounded-full px-2 py-1 text-[12px] font-semibold',
            isPositive ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400',
          )}
        >
          {direction === 'up' ? (
            <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ArrowDownRight className="h-3 w-3" aria-hidden="true" />
          )}
          {Math.abs(metric.changePct).toFixed(1).replace('.', ',')}%
        </span>
      </div>

      <p className="mt-4 text-[32px] font-bold leading-tight tabular-nums text-[color:var(--fh-text-primary)]">
        {metric.prefix}
        {formatMetricValue(animated, metric.decimals)}
        {metric.suffix}
      </p>
      <p className="mt-1 text-[14px] font-medium text-[color:var(--fh-text-secondary)]">{metric.label}</p>
      <p className="mt-2 text-[12px] text-[color:var(--fh-text-muted)]">{metric.description}</p>
    </div>
  );
}
