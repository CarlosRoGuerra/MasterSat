'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertTriangle,
  Car,
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  FileText,
  Filter,
  Loader2,
  Paperclip,
  Radio,
  ShieldAlert,
  Users,
  DollarSign,
  Wrench,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiFetch, type Page } from '@/lib/api';
import { buildSearchResultHref } from '@/lib/search-nav';
import type { TimelineCategory, TimelineEvent, TimelineSeverity } from '@/lib/domain-types';
import { parseError } from './helpers';

const LIMIT = 20;

type CategoryFilter = TimelineCategory | 'all';

const CATEGORY_ICON: Record<TimelineCategory, React.ComponentType<{ className?: string }>> = {
  cliente: Users,
  veiculo: Car,
  rastreador: Radio,
  contrato: FileText,
  documento: Paperclip,
  financeiro: DollarSign,
  os: Wrench,
  auditoria: ShieldAlert,
};

const SEVERITY_DOT: Record<TimelineSeverity, string> = {
  info: 'bg-sky-100 text-sky-600 dark:bg-sky-900/50 dark:text-sky-300',
  success: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/50 dark:text-emerald-300',
  warning: 'bg-amber-100 text-amber-600 dark:bg-amber-900/50 dark:text-amber-300',
  danger: 'bg-rose-100 text-rose-600 dark:bg-rose-900/50 dark:text-rose-300',
};

const CHIPS: { value: CategoryFilter; label: string; financeOnly?: boolean; adminOnly?: boolean }[] = [
  { value: 'all', label: 'Todos' },
  { value: 'cliente', label: 'Cliente' },
  { value: 'veiculo', label: 'Veículos' },
  { value: 'rastreador', label: 'Rastreadores' },
  { value: 'contrato', label: 'Contratos', financeOnly: true },
  { value: 'documento', label: 'Documentos' },
  { value: 'financeiro', label: 'Financeiro', financeOnly: true },
  { value: 'os', label: 'Ordens de serviço' },
  { value: 'auditoria', label: 'Auditoria', adminOnly: true },
];

const GROUP_ORDER = ['Hoje', 'Ontem', 'Esta semana', 'Este mês', 'Meses anteriores'] as const;

function groupLabel(iso: string): (typeof GROUP_ORDER)[number] {
  const d = new Date(iso);
  const now = new Date();
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diffDays === 0) return 'Hoje';
  if (diffDays === 1) return 'Ontem';
  if (diffDays > 1 && diffDays <= 7) return 'Esta semana';
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return 'Este mês';
  return 'Meses anteriores';
}

function groupEvents(events: TimelineEvent[]) {
  const map = new Map<string, TimelineEvent[]>();
  for (const e of events) {
    const label = groupLabel(e.occurred_at);
    if (!map.has(label)) map.set(label, []);
    map.get(label)!.push(e);
  }
  return GROUP_ORDER.filter((g) => map.has(g)).map((label) => ({ label, events: map.get(label)! }));
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

export function ClientHistoricoTab({
  clientId,
  token,
  canViewFinance,
  isAdmin,
  onExportPdf,
  onOpenBillings,
}: {
  clientId: number;
  token: string;
  canViewFinance: boolean;
  isAdmin: boolean;
  onExportPdf: () => void;
  onOpenBillings: () => void;
}) {
  const router = useRouter();
  const [category, setCategory] = useState<CategoryFilter>('all');
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [skip, setSkip] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  async function fetchPage(nextSkip: number, cat: CategoryFilter, replace: boolean) {
    if (!token) return;
    if (replace) setLoading(true); else setLoadingMore(true);
    setError('');
    try {
      const params = new URLSearchParams({ skip: String(nextSkip), limit: String(LIMIT) });
      if (cat !== 'all') params.set('category', cat);
      const page = await apiFetch<Page<TimelineEvent>>(`/clients/${clientId}/timeline?${params}`, {}, token);
      setEvents((prev) => (replace ? page.items : [...prev, ...page.items]));
      setTotal(page.total);
      setSkip(nextSkip + page.items.length);
    } catch (err) {
      setError(parseError(err));
    } finally {
      if (replace) setLoading(false); else setLoadingMore(false);
    }
  }

  useEffect(() => {
    setExpandedId(null);
    fetchPage(0, category, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, category, token]);

  const visibleChips = CHIPS.filter((c) => (!c.financeOnly || canViewFinance) && (!c.adminOnly || isAdmin));
  const groups = groupEvents(events);

  function openLink(event: TimelineEvent) {
    if (!event.link) return;
    router.push(buildSearchResultHref(event.link));
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Filter className="h-3.5 w-3.5 shrink-0 text-slate-400" />
          {visibleChips.map((chip) => (
            <button
              key={chip.value}
              type="button"
              onClick={() => setCategory(chip.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                category === chip.value
                  ? 'bg-brand-500 text-black'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
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
      ) : error ? (
        <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-sm text-rose-600 dark:text-rose-400">
          <AlertTriangle className="h-5 w-5" />
          {error}
        </div>
      ) : events.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhum evento registrado.</p>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <div key={group.label}>
              <p className="mb-2 text-3xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-600">
                {group.label}
              </p>
              <ol className="relative border-l border-slate-200 dark:border-slate-700">
                {group.events.map((event) => {
                  const Icon = CATEGORY_ICON[event.category];
                  const expanded = expandedId === event.id;
                  const hasDetails =
                    !!(event.metadata && Object.keys(event.metadata).length) ||
                    !!event.actor_name ||
                    !!event.link ||
                    event.category === 'financeiro';
                  return (
                    <li key={event.id} className="mb-4 ml-5">
                      <span className={`absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full ring-2 ring-white dark:ring-slate-950 ${SEVERITY_DOT[event.severity]}`}>
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                        <button
                          type="button"
                          onClick={() => hasDetails && setExpandedId(expanded ? null : event.id)}
                          className="flex w-full items-start justify-between gap-2 text-left"
                        >
                          <span>
                            <span className="block text-xs font-semibold text-slate-900 dark:text-white">{event.title}</span>
                            {event.description && (
                              <span className="mt-0.5 block text-xs text-slate-500">{event.description}</span>
                            )}
                            <time className="mt-1 block text-3xs text-slate-500">{formatTime(event.occurred_at)}</time>
                          </span>
                          {hasDetails && (
                            expanded ? <ChevronUp className="h-3.5 w-3.5 shrink-0 text-slate-400" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                          )}
                        </button>

                        {expanded && (
                          <div className="mt-2 space-y-1.5 border-t border-slate-200 pt-2 dark:border-slate-800">
                            {event.actor_name && (
                              <p className="text-3xs text-slate-500">
                                Responsável: <span className="text-slate-700 dark:text-slate-300">{event.actor_name}</span>
                              </p>
                            )}
                            {event.metadata && Object.entries(event.metadata).map(([key, value]) => (
                              <p key={key} className="text-3xs text-slate-500">
                                {key}: <span className="text-slate-700 dark:text-slate-300">{value}</span>
                              </p>
                            ))}
                            {event.category === 'financeiro' ? (
                              <button
                                type="button"
                                onClick={onOpenBillings}
                                className="inline-flex items-center gap-1 text-3xs font-semibold text-brand-700 hover:underline dark:text-brand-300"
                              >
                                <ExternalLink className="h-3 w-3" /> Ver cobranças
                              </button>
                            ) : event.link && (
                              <button
                                type="button"
                                onClick={() => openLink(event)}
                                className="inline-flex items-center gap-1 text-3xs font-semibold text-brand-700 hover:underline dark:text-brand-300"
                              >
                                <ExternalLink className="h-3 w-3" /> Ver registro
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
          ))}

          {events.length < total && (
            <div className="flex justify-center pt-1">
              <Button type="button" variant="secondary" disabled={loadingMore} onClick={() => fetchPage(skip, category, false)} className="gap-1.5">
                {loadingMore && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Carregar mais
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
