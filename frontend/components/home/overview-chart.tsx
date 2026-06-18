'use client';

import { useEffect, useRef, useState } from 'react';
import { formatMetricValue, type OverviewPoint } from '@/lib/mock-logistics';

const MARGIN = { top: 16, right: 8, bottom: 28, left: 8 };
const CHART_HEIGHT = 240;

const SERIES = [
  { key: 'deliveries' as const, label: 'Entregas realizadas', color: '#ffb800' },
  { key: 'activeVehicles' as const, label: 'Veículos ativos', color: 'rgba(248,250,252,0.55)' },
];

type Point = { x: number; y: number };

function scalePoints(data: OverviewPoint[], key: 'deliveries' | 'activeVehicles', chartW: number, chartH: number): Point[] {
  const values = data.map((d) => d[key]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = data.length > 1 ? chartW / (data.length - 1) : 0;
  const padding = chartH * 0.08;

  return data.map((d, i) => ({
    x: stepX * i,
    y: chartH - padding - ((d[key] - min) / range) * (chartH - padding * 2),
  }));
}

function buildLinePath(points: Point[]): string {
  return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
}

function buildAreaPath(points: Point[], chartH: number): string {
  if (points.length === 0) return '';
  const first = points[0];
  const last = points[points.length - 1];
  return `${buildLinePath(points)} L${last.x.toFixed(2)},${chartH} L${first.x.toFixed(2)},${chartH} Z`;
}

export function OverviewChart({ data }: { data: OverviewPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

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
  const stepX = data.length > 1 ? chartW / (data.length - 1) : 0;

  const deliveriesPoints = scalePoints(data, 'deliveries', chartW, chartH);
  const vehiclesPoints = scalePoints(data, 'activeVehicles', chartW, chartH);

  const tooltipLeftPct = stepX > 0 && hoveredIdx !== null
    ? Math.min(Math.max((stepX * hoveredIdx / chartW) * 100, 8), 78)
    : 0;

  return (
    <div ref={containerRef} className="w-full select-none">
      {/* Legenda */}
      <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5 text-[12px] font-medium text-[color:var(--fh-text-secondary)]">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
      </div>

      <div className="relative">
        <svg
          width="100%"
          height={chartH + MARGIN.top + MARGIN.bottom}
          viewBox={`0 0 ${width} ${chartH + MARGIN.top + MARGIN.bottom}`}
          role="img"
          aria-label={`Gráfico de evolução mensal de entregas realizadas e veículos ativos, de ${data[0]?.month} a ${data[data.length - 1]?.month}`}
          onMouseLeave={() => setHoveredIdx(null)}
        >
          <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
            <defs>
              <linearGradient id="overview-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ffb800" stopOpacity="0.35" />
                <stop offset="100%" stopColor="#ffb800" stopOpacity="0" />
              </linearGradient>
            </defs>

            {/* Linhas de grade horizontais */}
            {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
              <line
                key={pct}
                x1={0}
                x2={chartW}
                y1={chartH * pct}
                y2={chartH * pct}
                stroke="rgba(255,255,255,0.06)"
                strokeWidth={1}
              />
            ))}

            {/* Entregas: área + linha */}
            <path d={buildAreaPath(deliveriesPoints, chartH)} fill="url(#overview-area)" stroke="none" />
            <path
              d={buildLinePath(deliveriesPoints)}
              fill="none"
              stroke="#ffb800"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Veículos ativos: linha tracejada */}
            <path
              d={buildLinePath(vehiclesPoints)}
              fill="none"
              stroke="rgba(248,250,252,0.45)"
              strokeWidth={2}
              strokeDasharray="4 4"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Áreas de hover + marcadores */}
            {data.map((d, i) => {
              const x = stepX * i;
              const isHovered = hoveredIdx === i;
              return (
                <g key={d.month} onMouseEnter={() => setHoveredIdx(i)} className="cursor-pointer">
                  <rect x={x - stepX / 2} y={0} width={stepX || chartW} height={chartH} fill="transparent" />
                  {isHovered && (
                    <line x1={x} x2={x} y1={0} y2={chartH} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
                  )}
                  <circle cx={x} cy={deliveriesPoints[i].y} r={isHovered ? 4 : 0} fill="#ffb800" />
                  <circle cx={x} cy={vehiclesPoints[i].y} r={isHovered ? 4 : 0} fill="#f8fafc" />
                  <text x={x} y={chartH + 18} textAnchor="middle" fontSize={12} fill="rgba(248,250,252,0.48)">
                    {d.month}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {/* Tooltip */}
        {hoveredIdx !== null && (
          <div
            className="pointer-events-none absolute top-2 min-w-[160px] rounded-xl border border-[color:var(--fh-border)] bg-[#0b0d14]/95 px-3.5 py-3 text-[12px] shadow-2xl"
            style={{ left: `${tooltipLeftPct}%` }}
          >
            <p className="mb-1.5 font-semibold uppercase tracking-widest text-[color:var(--fh-text-muted)]">
              {data[hoveredIdx].month}
            </p>
            <p className="flex items-center justify-between gap-6 text-[color:var(--fh-text-primary)]">
              <span>Entregas</span>
              <span className="font-semibold tabular-nums">{formatMetricValue(data[hoveredIdx].deliveries)}</span>
            </p>
            <p className="mt-1 flex items-center justify-between gap-6 text-[color:var(--fh-text-secondary)]">
              <span>Veículos ativos</span>
              <span className="font-semibold tabular-nums">{formatMetricValue(data[hoveredIdx].activeVehicles)}</span>
            </p>
          </div>
        )}
      </div>

      {/* Tabela acessível para leitores de tela */}
      <table className="sr-only">
        <caption>Evolução mensal de entregas realizadas e veículos ativos</caption>
        <thead>
          <tr>
            <th scope="col">Mês</th>
            <th scope="col">Entregas realizadas</th>
            <th scope="col">Veículos ativos</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.month}>
              <th scope="row">{d.month}</th>
              <td>{d.deliveries}</td>
              <td>{d.activeVehicles}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
