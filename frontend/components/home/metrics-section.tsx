import { MetricCard } from './metric-card';
import { getHomeMetrics } from '@/lib/mock-logistics';

export function MetricsSection() {
  const metrics = getHomeMetrics();

  return (
    <section id="metricas" aria-labelledby="metrics-heading" className="mt-12 sm:mt-16">
      <div className="mb-6">
        <p className="text-[12px] font-semibold uppercase tracking-[0.28em] text-[color:var(--fh-accent)]">
          Indicadores
        </p>
        <h2 id="metrics-heading" className="mt-2 text-[24px] font-bold text-[color:var(--fh-text-primary)]">
          Performance da operação
        </h2>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:gap-6">
        {metrics.map((metric, i) => (
          <MetricCard key={metric.id} metric={metric} delayMs={i * 100} />
        ))}
      </div>
    </section>
  );
}
