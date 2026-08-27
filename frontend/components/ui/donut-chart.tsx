'use client';

import { useState } from 'react';
import { PieChart } from 'lucide-react';

export type DonutSlice = {
  label: string;
  value: number;
  color: string;
};

/**
 * Donut simples em SVG (mesma abordagem do bar-chart: sem dependência externa).
 * A legenda repete rótulo + valor, então a leitura não depende só da cor.
 */
export function DonutChart({
  slices,
  size = 220,
  thickness = 34,
  emptyMessage = 'Nenhuma nota no período',
}: {
  slices: DonutSlice[];
  size?: number;
  thickness?: number;
  emptyMessage?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  const total = slices.reduce((s, f) => s + f.value, 0);

  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 text-center" style={{ minHeight: size }}>
        <PieChart className="h-8 w-8 text-slate-300 dark:text-slate-600" />
        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{emptyMessage}</p>
      </div>
    );
  }

  const r = (size - thickness) / 2;
  const c = size / 2;
  const circ = 2 * Math.PI * r;

  let acumulado = 0;

  return (
    <div className="flex flex-wrap items-center justify-center gap-8">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
           aria-label={slices.map((s) => `${s.label}: ${s.value}`).join(', ')}>
        <circle cx={c} cy={c} r={r} fill="none" strokeWidth={thickness}
                className="stroke-slate-100 dark:stroke-slate-800" />
        {slices.filter((s) => s.value > 0).map((slice, i) => {
          const fracao = slice.value / total;
          const dash = fracao * circ;
          const offset = acumulado * circ;
          acumulado += fracao;
          const ativo = hovered === i;
          return (
            <circle
              key={slice.label}
              cx={c} cy={c} r={r} fill="none"
              stroke={slice.color}
              strokeWidth={ativo ? thickness + 6 : thickness}
              strokeDasharray={`${dash} ${circ - dash}`}
              strokeDashoffset={-offset}
              transform={`rotate(-90 ${c} ${c})`}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              style={{ transition: 'stroke-width 0.15s' }}
            />
          );
        })}
        <text x={c} y={c - 4} textAnchor="middle" className="fill-slate-900 dark:fill-white"
              fontSize="30" fontWeight="600">
          {total}
        </text>
        <text x={c} y={c + 18} textAnchor="middle" fill="#94a3b8" fontSize="12">
          {total === 1 ? 'nota' : 'notas'}
        </text>
      </svg>

      <ul className="space-y-2 text-sm">
        {slices.map((slice) => {
          const pct = total ? Math.round((slice.value / total) * 100) : 0;
          return (
            <li key={slice.label} className="flex items-center gap-2.5">
              <span className="h-3 w-3 shrink-0 rounded-sm" style={{ background: slice.color }} />
              <span className="text-slate-600 dark:text-slate-300">{slice.label}</span>
              <span className="ml-auto pl-4 font-semibold tabular-nums text-slate-900 dark:text-white">
                {slice.value}
              </span>
              <span className="w-10 text-right text-xs tabular-nums text-slate-500">{pct}%</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
