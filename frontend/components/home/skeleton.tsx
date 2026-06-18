export function ChartSkeleton() {
  return (
    <div
      className="rounded-2xl border p-6 backdrop-blur-xl"
      style={{ borderColor: 'var(--fh-border)', background: 'var(--fh-surface)' }}
      role="status"
      aria-label="Carregando gráfico de visão geral"
    >
      <div className="relative h-4 w-48 overflow-hidden rounded-full bg-white/[0.06]">
        <div className="animate-shimmer absolute inset-0" />
      </div>
      <div className="relative mt-8 h-[260px] w-full overflow-hidden rounded-xl bg-white/[0.04]">
        <div className="animate-shimmer absolute inset-0" />
      </div>
    </div>
  );
}
