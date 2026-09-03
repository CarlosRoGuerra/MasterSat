'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search,
  Users,
  Car,
  Radio,
  ClipboardList,
  FileText,
  Paperclip,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import clsx from 'clsx';

import { apiFetch } from '@/lib/api';
import { getAccessToken } from '@/lib/auth';
import { useDebouncedValue } from '@/lib/use-debounced-value';
import { buildSearchResultHref } from '@/lib/search-nav';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import type { GlobalSearchOut, SearchEntity, SearchResultItem } from '@/lib/domain-types';

const CATEGORY_META: Record<SearchEntity, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  client: { label: 'Clientes', icon: Users },
  vehicle: { label: 'Veículos', icon: Car },
  tracker: { label: 'Rastreadores', icon: Radio },
  service_order: { label: 'Ordens de serviço', icon: ClipboardList },
  contract: { label: 'Contratos', icon: FileText },
  document: { label: 'Documentos', icon: Paperclip },
};

const CATEGORY_ORDER: (keyof GlobalSearchOut)[] = [
  'clients',
  'vehicles',
  'trackers',
  'service_orders',
  'contracts',
  'documents',
];

const MIN_QUERY_LENGTH = 2;

function highlight(text: string, query: string) {
  const q = query.trim();
  if (!q) return text;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-brand-100 text-brand-800 dark:bg-brand-900/60 dark:text-brand-200">
        {text.slice(idx, idx + q.length)}
      </mark>
      {text.slice(idx + q.length)}
    </>
  );
}

export function GlobalSearch() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<GlobalSearchOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const requestIdRef = useRef(0);

  const debouncedQuery = useDebouncedValue(query);

  const flatResults = useMemo<SearchResultItem[]>(() => {
    if (!result) return [];
    return CATEGORY_ORDER.flatMap((key) => result[key]);
  }, [result]);

  const totalResults = flatResults.length;

  // Ctrl/Cmd+K abre de qualquer tela; Esc fecha (só quando aberta — não
  // interfere com outros usos de Esc, ex. fechar um Modal por cima dela).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(true);
        return;
      }
      if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  useEffect(() => {
    if (open) {
      const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(timer);
    }
    setQuery('');
    setResult(null);
    setError('');
    setActiveIndex(0);
  }, [open]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  useEffect(() => {
    const trimmed = debouncedQuery.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      setResult(null);
      setLoading(false);
      setError('');
      return;
    }

    const requestId = ++requestIdRef.current;
    const token = getAccessToken();
    setLoading(true);
    setError('');

    apiFetch<GlobalSearchOut>(`/search?q=${encodeURIComponent(trimmed)}`, {}, token || undefined)
      .then((data) => {
        // Ignora resposta de uma busca antiga que chegou depois de uma mais nova.
        if (requestId !== requestIdRef.current) return;
        setResult(data);
        setActiveIndex(0);
      })
      .catch((err) => {
        if (requestId !== requestIdRef.current) return;
        setError(err instanceof Error ? err.message : 'Não foi possível buscar.');
        setResult(null);
      })
      .finally(() => {
        if (requestId !== requestIdRef.current) return;
        setLoading(false);
      });
  }, [debouncedQuery]);

  const selectResult = useCallback((item: SearchResultItem) => {
    setOpen(false);
    router.push(buildSearchResultHref(item));
  }, [router]);

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (totalResults > 0) setActiveIndex((i) => (i + 1) % totalResults);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (totalResults > 0) setActiveIndex((i) => (i - 1 + totalResults) % totalResults);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = flatResults[activeIndex];
      if (item) selectResult(item);
    }
  }

  const showEmptyState = !loading && !error && result && totalResults === 0;
  const showPrompt = !loading && !error && !result && query.trim().length < MIN_QUERY_LENGTH;

  let rowCursor = -1;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-9 shrink-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-2 text-sm text-slate-500 transition hover:border-slate-300 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200 sm:w-56 sm:px-3"
        aria-label="Busca global"
      >
        <Search className="h-4 w-4 shrink-0" />
        <span className="hidden flex-1 truncate text-left sm:inline">Buscar…</span>
        <kbd className="hidden shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-3xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400 sm:inline-block">
          Ctrl K
        </kbd>
      </button>

      {open && (
        <div className="fixed inset-0 z-[110] flex items-start justify-center bg-slate-950/40 px-4 pt-[12vh] backdrop-blur-sm">
          <div
            ref={containerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Busca global"
            className="flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-elevated dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
              <Search className="h-4 w-4 shrink-0 text-slate-400" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onInputKeyDown}
                placeholder="Buscar cliente, veículo, placa, IMEI, CPF/CNPJ…"
                autoComplete="off"
                className="flex-1 border-0 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400 dark:text-white dark:placeholder:text-slate-500"
              />
              {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" />}
              <kbd className="hidden shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-3xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400 sm:inline-block">
                Esc
              </kbd>
            </div>

            <div className="flex-1 overflow-y-auto p-2">
              {showPrompt && (
                <p className="px-3 py-6 text-center text-sm text-slate-500 dark:text-slate-400">
                  Digite ao menos {MIN_QUERY_LENGTH} caracteres para buscar.
                </p>
              )}

              {error && (
                <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-sm text-rose-600 dark:text-rose-400">
                  <AlertTriangle className="h-5 w-5" />
                  {error}
                </div>
              )}

              {showEmptyState && (
                <p className="px-3 py-6 text-center text-sm text-slate-500 dark:text-slate-400">
                  Não encontramos resultados para &quot;{debouncedQuery.trim()}&quot;
                </p>
              )}

              {result && totalResults > 0 && CATEGORY_ORDER.map((key) => {
                const items = result[key];
                if (items.length === 0) return null;
                const entity = items[0].entity;
                const meta = CATEGORY_META[entity];
                const Icon = meta.icon;
                return (
                  <div key={key} className="mb-2 last:mb-0">
                    <p className="px-3 pb-1 pt-2 text-3xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-600">
                      {meta.label}
                    </p>
                    <div className="space-y-0.5">
                      {items.map((item) => {
                        rowCursor += 1;
                        const rowIndex = rowCursor;
                        const active = rowIndex === activeIndex;
                        return (
                          <button
                            key={`${item.entity}-${item.id}`}
                            type="button"
                            onMouseEnter={() => setActiveIndex(rowIndex)}
                            onClick={() => selectResult(item)}
                            className={clsx(
                              'flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left transition-colors',
                              active ? 'bg-brand-50 dark:bg-brand-950/40' : 'hover:bg-slate-50 dark:hover:bg-slate-800/70',
                            )}
                          >
                            <Icon className="h-4 w-4 shrink-0 text-slate-400" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-slate-900 dark:text-white">
                                {highlight(item.title, debouncedQuery)}
                              </span>
                              {item.subtitle && (
                                <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                                  {item.subtitle}
                                </span>
                              )}
                            </span>
                            {item.status && (
                              <Badge variant={statusVariant(item.status)} className="shrink-0">
                                {statusLabel(item.status)}
                              </Badge>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
