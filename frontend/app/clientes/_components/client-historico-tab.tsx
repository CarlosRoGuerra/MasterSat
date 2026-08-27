import { AlertCircle, CheckCircle, Clock, Download, FileText, Wrench } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { TimelineEvent } from './types';

const KIND_CONFIG: Record<TimelineEvent['kind'], { bg: string; icon: React.ReactNode }> = {
  contract:        { bg: 'bg-brand-100 dark:bg-brand-900/50',   icon: <FileText className="h-3.5 w-3.5 text-brand-700 dark:text-brand-300" /> },
  os:              { bg: 'bg-slate-100 dark:bg-slate-800',        icon: <Wrench className="h-3.5 w-3.5 text-slate-600 dark:text-slate-300" /> },
  billing_paid:    { bg: 'bg-emerald-100 dark:bg-emerald-900/50', icon: <CheckCircle className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-300" /> },
  billing_overdue: { bg: 'bg-rose-100 dark:bg-rose-900/50',       icon: <AlertCircle className="h-3.5 w-3.5 text-rose-600 dark:text-rose-300" /> },
  billing_pending: { bg: 'bg-amber-100 dark:bg-amber-900/50',     icon: <Clock className="h-3.5 w-3.5 text-amber-600 dark:text-amber-300" /> },
};

export function ClientHistoricoTab({
  loading,
  events,
  onExportPdf,
}: {
  loading: boolean;
  events: TimelineEvent[];
  onExportPdf: () => void;
}) {
  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button type="button" variant="secondary" onClick={onExportPdf} className="gap-1.5">
          <Download className="h-4 w-4" /> Exportar PDF
        </Button>
      </div>
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex gap-3">
              <div className="h-7 w-7 shrink-0 animate-pulse rounded-full bg-slate-100 dark:bg-slate-800" />
              <div className="flex-1 space-y-1.5 pt-1">
                <div className="h-3 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
                <div className="h-3 w-1/2 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
              </div>
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhum evento registrado.</p>
      ) : (
        <ol className="relative border-l border-slate-200 dark:border-slate-700">
          {events.map((event) => {
            const cfg = KIND_CONFIG[event.kind];
            return (
              <li key={event.key} className="mb-4 ml-5">
                <span className={`absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full ring-2 ring-white dark:ring-slate-950 ${cfg.bg}`}>{cfg.icon}</span>
                <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                  <p className="text-xs font-semibold text-slate-900 dark:text-white">{event.title}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{event.subtitle}</p>
                  <time className="mt-1 block text-3xs text-slate-500">{event.date}</time>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
