import { MetricCard } from './metric-card';
import { getHomeMetrics } from '@/lib/mock-logistics';

export function MetricsSection() {
  const metrics = getHomeMetrics();

  return (
    <section id="metricas" aria-labelledby="metrics-heading" className="mt-12 sm:mt-16">
      <div className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[12px] font-semibold uppercase tracking-[0.28em] text-[color:var(--fh-accent)]">
            Indicadores
          </p>
          <span className="inline-flex items-center rounded-full border border-[color:var(--fh-border)] bg-[color:var(--fh-accent-soft)] px-2.5 py-1 text-[11px] font-medium text-[color:var(--fh-text-secondary)]">
            Dados ilustrativos
          </span>
        </div>
        <h2 id="metrics-heading" className="mt-2 text-[24px] font-bold text-[color:var(--fh-text-primary)]">
          Performance da operação
        </h2>
        <p className="mt-1 text-[13px] text-[color:var(--fh-text-muted)]">
          Números de demonstração, sem vínculo com dados reais de clientes.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:gap-6">
        {metrics.map((metric, i) => (
          <MetricCard key={metric.id} metric={metric} delayMs={i * 100} />
        ))}
      </div>
    </section>
  );
}
