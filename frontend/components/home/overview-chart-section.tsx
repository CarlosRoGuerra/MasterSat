'use client';

import clsx from 'clsx';
import { OverviewChart } from './overview-chart';
import { useReveal } from './use-reveal';
import { getOverviewSeries } from '@/lib/mock-logistics';

export default function OverviewChartSection() {
  const { ref, visible } = useReveal<HTMLDivElement>();
  const data = getOverviewSeries();

  return (
    <section id="visao-geral" aria-labelledby="overview-heading" className="mt-12 sm:mt-16">
      <div className="mb-6">
        <p className="text-[12px] font-semibold uppercase tracking-[0.28em] text-[color:var(--fh-accent)]">
          Visão geral
        </p>
        <h2 id="overview-heading" className="mt-2 text-[24px] font-bold text-[color:var(--fh-text-primary)]">
          Operação nos últimos 12 meses
        </h2>
      </div>
      <div
        ref={ref}
        className={clsx(
          'rounded-2xl border border-[color:var(--fh-border)] bg-[color:var(--fh-surface)] p-6 backdrop-blur-xl transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)]',
          visible ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0',
        )}
      >
        <OverviewChart data={data} />
      </div>
    </section>
  );
}
