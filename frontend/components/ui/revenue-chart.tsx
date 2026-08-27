'use client';

import { useState, useRef, useEffect } from 'react';

/* ── Types ─────────────────────────────────────────────────────────────── */
export type RevenueMonth = {
  label: string;        // "05/2026"
  total_emitido: number;
  total_recebido: number;
  total_aberto: number;
};

type Series = { key: keyof RevenueMonth; label: string; color: string; lightColor: string };

const SERIES: Series[] = [
  { key: 'total_emitido',  label: 'Emitido',    color: '#3b82f6', lightColor: '#93c5fd' },
  { key: 'total_recebido', label: 'Recebido',   color: '#10b981', lightColor: '#6ee7b7' },
  { key: 'total_aberto',   label: 'Em aberto',  color: '#f43f5e', lightColor: '#fda4af' },
];

const MARGIN = { top: 16, right: 12, bottom: 40, left: 64 };
const CHART_HEIGHT = 220;
const BAR_GAP = 2;
const GROUP_GAP = 8;
const Y_TICKS = 5;

function fmtBRL(v: number) {
  if (v >= 1_000_000) return `R$ ${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000)     return `R$ ${(v / 1_000).toFixed(0)}K`;
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
}

function fmtFull(v: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
}

function niceMax(rawMax: number): number {
  if (rawMax === 0) return 100;
  const mag = Math.pow(10, Math.floor(Math.log10(rawMax)));
  return Math.ceil(rawMax / mag) * mag;
}

/* ── Tooltip ───────────────────────────────────────────────────────────── */
function Tooltip({ month, x, y, chartWidth }: {
  month: RevenueMonth; x: number; y: number; chartWidth: number;
}) {
  const W = 180;
  const left = x + W > chartWidth ? x - W - 8 : x + 8;
  return (
    <div
      className="pointer-events-none absolute z-50 rounded-xl border border-slate-200 bg-white px-3.5 py-3 shadow-elevated dark:border-slate-700 dark:bg-slate-900"
      style={{ top: y, left, minWidth: W }}
    >
      <p className="mb-2 text-2xs font-semibold uppercase tracking-widest text-slate-500">
        {month.label}
      </p>
      {SERIES.map(s => (
        <div key={s.key} className="flex items-center justify-between gap-6">
          <span className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
            {s.label}
          </span>
          <span className="text-xs font-semibold tabular-nums text-slate-900 dark:text-white">
            {fmtFull(month[s.key] as number)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────────────────── */
export function RevenueChart({ data }: { data: RevenueMonth[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  /* ── Responsive width ── */
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const chartW = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const chartH = CHART_HEIGHT;

  /* ── Scale ── */
  const rawMax = data.reduce((m, d) =>
    Math.max(m, d.total_emitido, d.total_recebido, d.total_aberto), 0);
  const maxVal = niceMax(rawMax);

  const n = data.length;
  const groupW = n > 0 ? (chartW - GROUP_GAP * (n - 1)) / n : 0;
  const barW = Math.max(2, (groupW - BAR_GAP * (SERIES.length - 1)) / SERIES.length);

  function barHeight(v: number) { return (v / maxVal) * chartH; }
  function barX(gi: number, si: number) {
    return gi * (groupW + GROUP_GAP) + si * (barW + BAR_GAP);
  }
  function barY(v: number) { return chartH - barHeight(v); }

  /* ── Y-axis ticks ── */
  const ticks = Array.from({ length: Y_TICKS + 1 }, (_, i) => (maxVal * i) / Y_TICKS);

  /* ── Empty state ── */
  if (data.length === 0) {
    return (
      <div className="flex h-[260px] flex-col items-center justify-center gap-2">
        <svg className="h-10 w-10 text-slate-200 dark:text-slate-700" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
          <rect x="3" y="12" width="4" height="9" rx="1" />
          <rect x="10" y="7" width="4" height="14" rx="1" />
          <rect x="17" y="3" width="4" height="18" rx="1" />
        </svg>
        <p className="text-sm font-medium text-slate-500">Sem dados para o período</p>
        <p className="text-xs text-slate-300 dark:text-slate-600">Selecione outro intervalo ou aguarde cobranças serem geradas</p>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full select-none">
      {/* Legend */}
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        {SERIES.map(s => (
          <span key={s.key} className="flex items-center gap-1.5 text-2xs font-medium text-slate-500 dark:text-slate-400">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>

      {/* SVG chart */}
      <svg
        width="100%"
        height={CHART_HEIGHT + MARGIN.top + MARGIN.bottom}
        viewBox={`0 0 ${width} ${CHART_HEIGHT + MARGIN.top + MARGIN.bottom}`}
        onMouseLeave={() => setHoveredIdx(null)}
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>

          {/* Y-axis gridlines + labels */}
          {ticks.map((tick, i) => {
            const y = chartH - (tick / maxVal) * chartH;
            return (
              <g key={i}>
                <line
                  x1={0} y1={y} x2={chartW} y2={y}
                  stroke={i === 0 ? '#94a3b8' : '#e2e8f0'}
                  strokeWidth={i === 0 ? 1 : 0.5}
                  className="dark:stroke-slate-700"
                />
                <text
                  x={-8} y={y + 4}
                  textAnchor="end"
                  fontSize={10}
                  fill="#94a3b8"
                  className="font-mono"
                >
                  {fmtBRL(tick)}
                </text>
              </g>
            );
          })}

          {/* Bars */}
          {data.map((month, gi) => (
            <g
              key={month.label}
              onMouseEnter={(e) => {
                const rect = containerRef.current?.getBoundingClientRect();
                const svgRect = (e.currentTarget.closest('svg') as SVGSVGElement)?.getBoundingClientRect();
                if (rect && svgRect) {
                  const x = MARGIN.left + gi * (groupW + GROUP_GAP) + groupW / 2;
                  setTooltipPos({
                    x: (x / width) * svgRect.width,
                    y: MARGIN.top,
                  });
                }
                setHoveredIdx(gi);
              }}
            >
              {/* Hover highlight band */}
              {hoveredIdx === gi && (
                <rect
                  x={gi * (groupW + GROUP_GAP) - 4}
                  y={0}
                  width={groupW + 8}
                  height={chartH}
                  fill="currentColor"
                  className="text-slate-100 dark:text-slate-800/60"
                  rx={4}
                />
              )}

              {/* 3 bars per group */}
              {SERIES.map((s, si) => {
                const v = month[s.key] as number;
                const h = barHeight(v);
                return (
                  <rect
                    key={s.key}
                    x={barX(gi, si)}
                    y={barY(v)}
                    width={barW}
                    height={h}
                    rx={Math.min(3, barW / 3)}
                    fill={hoveredIdx === gi ? s.color : s.lightColor}
                    style={{ transition: 'fill 0.15s, y 0.2s, height 0.2s' }}
                  />
                );
              })}

              {/* X-axis label */}
              <text
                x={gi * (groupW + GROUP_GAP) + groupW / 2}
                y={chartH + 20}
                textAnchor="middle"
                fontSize={10}
                fill="#94a3b8"
              >
                {month.label.slice(0, 5)}
              </text>
            </g>
          ))}
        </g>
      </svg>

      {/* Floating tooltip */}
      {hoveredIdx !== null && (
        <Tooltip
          month={data[hoveredIdx]}
          x={tooltipPos.x}
          y={tooltipPos.y}
          chartWidth={width}
        />
      )}
    </div>
  );
}
