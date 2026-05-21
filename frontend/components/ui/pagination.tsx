'use client';

import { useState } from 'react';
import clsx from 'clsx';
import { ChevronLeft, ChevronRight } from 'lucide-react';

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePagination<T>(items: T[], pageSize = 20) {
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const slice = items.slice((safePage - 1) * pageSize, safePage * pageSize);

  return {
    page: safePage,
    setPage,
    totalPages,
    slice,
    total: items.length,
    pageSize,
    start: items.length === 0 ? 0 : (safePage - 1) * pageSize + 1,
    end: Math.min(safePage * pageSize, items.length),
    /** Reset to first page — call when filters change */
    reset: () => setPage(1),
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Pagination({
  page,
  totalPages,
  total,
  start,
  end,
  onPage,
  className,
}: {
  page: number;
  totalPages: number;
  total: number;
  start: number;
  end: number;
  onPage: (p: number) => void;
  className?: string;
}) {
  if (totalPages <= 1 && total <= 20) return null;

  const pages = buildPageRange(page, totalPages);

  return (
    <div className={clsx('flex items-center justify-between gap-4 border-t border-slate-100 pt-3 dark:border-slate-800', className)}>
      <p className="text-xs text-slate-400 dark:text-slate-500">
        {total === 0 ? '0 registros' : `${start}–${end} de ${total}`}
      </p>
      {totalPages > 1 && (
        <div className="flex items-center gap-1">
          <NavBtn onClick={() => onPage(page - 1)} disabled={page <= 1} label="Página anterior">
            <ChevronLeft className="h-3.5 w-3.5" />
          </NavBtn>
          {pages.map((p, i) =>
            p === '...' ? (
              <span key={`e-${i}`} className="px-1 text-xs text-slate-400">…</span>
            ) : (
              <NavBtn key={p} onClick={() => onPage(p as number)} active={p === page}>
                {p}
              </NavBtn>
            ),
          )}
          <NavBtn onClick={() => onPage(page + 1)} disabled={page >= totalPages} label="Próxima página">
            <ChevronRight className="h-3.5 w-3.5" />
          </NavBtn>
        </div>
      )}
    </div>
  );
}

function NavBtn({
  children,
  onClick,
  disabled,
  active,
  label,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className={clsx(
        'flex h-7 min-w-[28px] items-center justify-center rounded-lg px-1.5 text-xs font-medium transition-colors',
        active
          ? 'bg-brand-700 text-white dark:bg-brand-500'
          : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800',
        disabled && 'cursor-not-allowed opacity-40',
      )}
    >
      {children}
    </button>
  );
}

function buildPageRange(current: number, total: number): (number | '...')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const result: (number | '...')[] = [1];
  if (current > 3) result.push('...');
  for (let p = Math.max(2, current - 1); p <= Math.min(total - 1, current + 1); p++) {
    result.push(p);
  }
  if (current < total - 2) result.push('...');
  result.push(total);
  return result;
}
