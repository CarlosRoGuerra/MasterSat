'use client';

import { useState } from 'react';
import { Download } from 'lucide-react';
import clsx from 'clsx';
import { downloadExport, exportFilename, ExportFormat } from '@/lib/export';

export function ExportButton({
  path,
  params = {},
  basename,
  token,
  className,
}: {
  path: string;         // ex: 'exports/clients'
  params?: Record<string, string>;
  basename: string;     // ex: 'clientes'
  token: string;
  className?: string;
}) {
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  async function handleExport(fmt: ExportFormat) {
    setOpen(false);
    setLoading(true);
    try {
      await downloadExport(
        path,
        { ...params, fmt },
        token,
        exportFilename(basename, fmt),
      );
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao exportar');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={clsx('relative', className)}>
      <button
        type="button"
        disabled={loading}
        onClick={() => setOpen((p) => !p)}
        className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        <Download className={clsx('h-3.5 w-3.5', loading && 'animate-bounce')} />
        {loading ? 'Exportando…' : 'Exportar'}
      </button>

      {open && (
        <>
          {/* Overlay para fechar */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-50 mt-1 w-40 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-elevated dark:border-slate-700 dark:bg-slate-900">
            <button
              type="button"
              onClick={() => handleExport('csv')}
              className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <span className="text-xs font-bold text-emerald-600">CSV</span>
              Download CSV
            </button>
            <button
              type="button"
              onClick={() => handleExport('xlsx')}
              className="flex w-full items-center gap-2 border-t border-slate-100 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <span className="text-xs font-bold text-blue-600">XLS</span>
              Download Excel
            </button>
          </div>
        </>
      )}
    </div>
  );
}
