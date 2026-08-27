'use client';

import { useState } from 'react';
import { BarChart2 } from 'lucide-react';

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
  onSelectPeriod?: () => void;
};

const W = 600;
const PAD = { top: 32, right: 8, bottom: 56, left: 8 };

export function BarChart({
  items,
  formatValue = (v) => String(v),
  primaryColor = '#FFB800',
  secondaryColor = '#E09F00',
  height = 200,
  showSecondary = false,
  emptyMessage,
  onSelectPeriod,
}: BarChartProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  const allZero = items.length === 0 || items.every((i) => i.value === 0 && !i.secondaryValue);

  if (allZero) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-8 text-center" style={{ minHeight: height }}>
        <BarChart2 className="h-8 w-8 text-slate-300 dark:text-slate-600" />
        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
          {emptyMessage ?? 'Nenhum dado no período'}
        </p>
        {onSelectPeriod && (
          <button
            type="button"
            onClick={onSelectPeriod}
            className="mt-1 text-xs font-medium text-brand-700 hover:underline dark:text-brand-400"
          >
            Selecionar outro período
          </button>
        )}
      </div>
    );
  }

  const chartH = Math.max(height - PAD.top - PAD.bottom, 80);
  const chartW = W - PAD.left - PAD.right;
  const maxVal = Math.max(
    ...items.map((i) => Math.max(i.value, showSecondary ? (i.secondaryValue ?? 0) : 0)),
    1,
  );
  const step = chartW / items.length;
  const barW = Math.min(step * 0.45, 40);

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      className="w-full overflow-visible"
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

        const primaryH = Math.max((item.value / maxVal) * chartH, item.value > 0 ? 4 : 0);
        const primaryY = PAD.top + chartH - primaryH;

        const secondaryH = showSecondary && item.secondaryValue
          ? Math.max((item.secondaryValue / maxVal) * chartH, 4)
          : 0;
        const secondaryY = PAD.top + chartH - secondaryH;

        const barCx = showSecondary ? cx : cx - barW / 2;

        // Tooltip box dimensions
        const tooltipLabel = formatValue(item.value);
        const tooltipW = Math.max(tooltipLabel.length * 7, 40);
        const tooltipX = Math.min(Math.max(cx - tooltipW / 2, PAD.left), W - PAD.right - tooltipW);
        const tooltipY = primaryY - 30;

        return (
          <g
            key={i}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          >
            {/* Secondary bar */}
            {showSecondary && secondaryH > 0 && (
              <rect
                x={cx - barW / 2 - barW * 0.6}
                y={secondaryY}
                width={barW}
                height={secondaryH}
                rx={3}
                fill={secondaryColor}
                opacity={isHov ? 1 : 0.75}
                style={{ transition: 'opacity 0.15s' }}
              />
            )}

            {/* Primary bar */}
            <rect
              x={barCx}
              y={primaryY}
              width={barW}
              height={primaryH}
              rx={3}
              fill={isHov ? '#CC9200' : primaryColor}
              style={{ transition: 'fill 0.15s' }}
            />

            {/* Value label above bar — always visible */}
            {item.value > 0 && !isHov && (
              <text
                x={cx}
                y={primaryY - 5}
                textAnchor="middle"
                fontSize="10"
                fontWeight="600"
                fill="#94a3b8"
              >
                {formatValue(item.value)}
              </text>
            )}

            {/* Tooltip box on hover */}
            {isHov && item.value > 0 && (
              <g>
                <rect
                  x={tooltipX}
                  y={tooltipY - 2}
                  width={tooltipW + 10}
                  height={20}
                  rx={4}
                  fill="#1e293b"
                  opacity={0.92}
                />
                <text
                  x={tooltipX + (tooltipW + 10) / 2}
                  y={tooltipY + 11}
                  textAnchor="middle"
                  fontSize="11"
                  fontWeight="700"
                  fill="white"
                >
                  {tooltipLabel}
                </text>
              </g>
            )}

            {/* X-axis label — rotated 45° */}
            <text
              x={cx}
              y={PAD.top + chartH + 16}
              textAnchor="end"
              fontSize="10"
              fill="#94a3b8"
              transform={`rotate(-40 ${cx} ${PAD.top + chartH + 16})`}
            >
              {item.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
