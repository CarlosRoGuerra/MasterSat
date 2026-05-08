'use client';

import { useState } from 'react';

export type BarItem = {
  label: string;
  value: number;
  secondaryValue?: number;
};

type BarChartProps = {
  items: BarItem[];
  formatValue?: (v: number) => string;
  primaryColor?: string;
  secondaryColor?: string;
  height?: number;
  showSecondary?: boolean;
  emptyMessage?: string;
};

const W = 600;
const PAD = { top: 28, right: 8, bottom: 36, left: 8 };

export function BarChart({
  items,
  formatValue = (v) => String(v),
  primaryColor = '#184062',
  secondaryColor = '#93b8d8',
  height = 160,
  showSecondary = false,
  emptyMessage = 'Sem dados disponíveis.',
}: BarChartProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  if (!items.length) {
    return <p className="text-sm text-slate-400 dark:text-slate-500">{emptyMessage}</p>;
  }

  const chartH = height - PAD.top - PAD.bottom;
  const chartW = W - PAD.left - PAD.right;
  const maxVal = Math.max(
    ...items.map((i) => Math.max(i.value, showSecondary ? (i.secondaryValue ?? 0) : 0)),
    1,
  );
  const step = chartW / items.length;
  const barW = Math.min(step * 0.45, 36);

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      className="w-full"
      style={{ height }}
      aria-hidden="true"
    >
      {/* Horizontal grid lines */}
      {[0.25, 0.5, 0.75, 1].map((pct) => {
        const y = PAD.top + chartH * (1 - pct);
        return (
          <line
            key={pct}
            x1={PAD.left}
            x2={W - PAD.right}
            y1={y}
            y2={y}
            stroke="currentColor"
            strokeWidth="0.5"
            className="text-slate-200 dark:text-slate-700"
          />
        );
      })}

      {items.map((item, i) => {
        const cx = PAD.left + step * i + step / 2;
        const isHov = hovered === i;

        const primaryH = Math.max((item.value / maxVal) * chartH, item.value > 0 ? 2 : 0);
        const primaryY = PAD.top + chartH - primaryH;

        const secondaryH = showSecondary && item.secondaryValue
          ? Math.max((item.secondaryValue / maxVal) * chartH, 2)
          : 0;
        const secondaryY = PAD.top + chartH - secondaryH;

        return (
          <g
            key={i}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          >
            {/* Secondary bar (behind) */}
            {showSecondary && secondaryH > 0 && (
              <rect
                x={cx - barW / 2 - (showSecondary ? barW * 0.6 : 0)}
                y={secondaryY}
                width={barW}
                height={secondaryH}
                rx={3}
                fill={secondaryColor}
                opacity={isHov ? 1 : 0.7}
                style={{ transition: 'opacity 0.15s' }}
              />
            )}

            {/* Primary bar */}
            <rect
              x={showSecondary ? cx : cx - barW / 2}
              y={primaryY}
              width={barW}
              height={primaryH}
              rx={3}
              fill={isHov ? '#102d45' : primaryColor}
              style={{ transition: 'fill 0.15s' }}
            />

            {/* Tooltip value on hover */}
            {isHov && item.value > 0 && (
              <text
                x={showSecondary ? cx + barW / 2 : cx}
                y={primaryY - 6}
                textAnchor="middle"
                fontSize="11"
                fontWeight="700"
                fill={primaryColor}
              >
                {formatValue(item.value)}
              </text>
            )}

            {/* X-axis label */}
            <text
              x={cx}
              y={height - PAD.bottom + 16}
              textAnchor="middle"
              fontSize="10"
              fill="#94a3b8"
            >
              {item.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
