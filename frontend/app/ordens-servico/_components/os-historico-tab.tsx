'use client';

import { Badge, statusVariant } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/empty-state';
import { OrderLog, formatDateTimeLabel, statusOptions } from './types';

export function OsHistoricoTab({ logs }: { logs: OrderLog[] }) {
  if (logs.length === 0) {
    return <EmptyState title="Sem histórico" description="Nenhuma mudança de status registrada." />;
  }

  return (
    <ol className="relative border-l border-slate-200 dark:border-slate-700">
      {logs.map((log) => (
        <li key={log.id} className="mb-4 ml-5">
          <span className="absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 ring-2 ring-white dark:bg-slate-800 dark:ring-slate-950">
            <span className="h-2 w-2 rounded-full bg-brand-500" />
          </span>
          <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
            <p className="text-xs font-semibold text-slate-900 dark:text-white">
              {log.previous_status && <><Badge variant={statusVariant(log.previous_status)}>{statusOptions.find((x) => x.value === log.previous_status)?.label}</Badge>{' → '}</>}
              <Badge variant={statusVariant(log.new_status)}>{statusOptions.find((x) => x.value === log.new_status)?.label}</Badge>
            </p>
            <time className="mt-0.5 block text-3xs text-slate-500">{formatDateTimeLabel(log.created_at)} · {log.changed_by_name ?? 'Sistema'}</time>
            {log.notes && <p className="mt-1 text-xs text-slate-500">{log.notes}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}
