'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
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
  CheckCircle2,
  X,
} from 'lucide-react';
import clsx from 'clsx';

import { apiFetch } from '@/lib/api';
import { getAccessToken } from '@/lib/auth';
import { useCurrentUser } from '@/lib/use-current-user';
import { useDebouncedValue } from '@/lib/use-debounced-value';
import { buildSearchResultHref } from '@/lib/search-nav';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { NAV_GROUPS, type NavItem } from '@/lib/nav-items';
import {
  ASSISTANT_ACTIONS,
  buildActionHref,
  filterActionsByRole,
  type AssistantActionDef,
  type PaletteCtx,
} from '@/lib/assistant-actions';
import { useAssistantAvailableActions, type ContextualAction } from '@/lib/assistant-context';
import { fuzzyScore } from '@/lib/fuzzy-match';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { PaletteActionRow, PaletteSectionHeader } from '@/components/assistant-palette-sections';
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

const ENTITY_TO_RESULT_KEY: Record<SearchEntity, keyof GlobalSearchOut> = {
  client: 'clients',
  vehicle: 'vehicles',
  tracker: 'trackers',
  service_order: 'service_orders',
  contract: 'contracts',
  document: 'documents',
};

const ENTITY_ARTICLE_LABEL: Record<SearchEntity, string> = {
  client: 'o cliente',
  vehicle: 'o veículo',
  tracker: 'o rastreador',
  service_order: 'a ordem de serviço',
  contract: 'o contrato',
  document: 'o documento',
};

const MIN_QUERY_LENGTH = 2;
const MAX_ACTION_ROWS = 6;
const MAX_NAV_ROWS = 6;

type PaletteMode = 'palette' | 'pick-record';

type FlatRow =
  | { rowKind: 'static'; action: AssistantActionDef }
  | { rowKind: 'contextual'; action: ContextualAction }
  | { rowKind: 'nav'; item: NavItem }
  | { rowKind: 'result'; item: SearchResultItem };

type Feedback = { type: 'success' | 'error'; message: string; retry?: () => void };

function scoreAgainst(query: string, label: string, keywords: string[] = []): number {
  return fuzzyScore(query, [label, ...keywords]);
}

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
  const pathname = usePathname();
  const { data: user } = useCurrentUser();
  const contextualActions = useAssistantAvailableActions();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<GlobalSearchOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  const [mode, setMode] = useState<PaletteMode>('palette');
  const [pendingAction, setPendingAction] = useState<AssistantActionDef | null>(null);
  const [paletteCtx, setPaletteCtx] = useState<PaletteCtx>({ pathname: '' });

  const [confirmAction, setConfirmAction] = useState<ContextualAction | null>(null);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const requestIdRef = useRef(0);

  const debouncedQuery = useDebouncedValue(query);
  const trimmedQuery = query.trim();
  const hasQuery = trimmedQuery.length > 0;

  const roleFilteredActions = useMemo(
    () => filterActionsByRole(ASSISTANT_ACTIONS, user?.role),
    [user?.role],
  );

  // Ctrl/Cmd+K abre de qualquer tela; Esc fecha (só quando aberta — não
  // interfere com outros usos de Esc, ex. fechar um Modal por cima dela).
  // Esc sempre fecha a paleta inteira, mesmo em modo pick-record — não há
  // "voltar um passo", só abrir/fechar.
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
      // Lido só no momento em que a paleta abre — o suficiente para prefill
      // (ex.: "Novo veículo" dentro de /clientes?focus=42), sem precisar de
      // useSearchParams() aqui (que exigiria envolver TODA página que usa
      // PageShell num <Suspense>, não só as 4 que já lidam com deep-link).
      const params = new URLSearchParams(window.location.search);
      const focusId = params.get('focus');
      const focusIdNum = focusId ? Number(focusId) : undefined;
      setPaletteCtx({
        pathname,
        focusClientId: pathname === '/clientes' ? focusIdNum : undefined,
        focusVehicleId: pathname === '/veiculos' ? focusIdNum : undefined,
      });
      return () => window.clearTimeout(timer);
    }
    setQuery('');
    setResult(null);
    setError('');
    setActiveIndex(0);
    setMode('palette');
    setPendingAction(null);
    setConfirmAction(null);
    setConfirmLoading(false);
    setFeedback(null);
  }, [open, pathname]);

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
    setActiveIndex(0);
  }, [query, mode]);

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

  const visibleCategoryOrder = useMemo(() => {
    if (mode === 'pick-record' && pendingAction?.pickEntity) {
      return [ENTITY_TO_RESULT_KEY[pendingAction.pickEntity]];
    }
    return CATEGORY_ORDER;
  }, [mode, pendingAction]);

  const entityRows = useMemo<FlatRow[]>(() => {
    if (!result) return [];
    return visibleCategoryOrder.flatMap((key) =>
      result[key].map((item) => ({ rowKind: 'result' as const, item })),
    );
  }, [result, visibleCategoryOrder]);

  const entityResultsCount = entityRows.length;

  const actionSectionLabel = contextualActions.length > 0 && !hasQuery ? 'Para esta tela' : 'Ações';

  const actionRows = useMemo<FlatRow[]>(() => {
    if (mode !== 'palette') return [];

    if (!hasQuery) {
      if (contextualActions.length > 0) {
        return contextualActions.map((action) => ({ rowKind: 'contextual' as const, action }));
      }
      return roleFilteredActions
        .filter((action) => action.category === 'criar')
        .slice(0, 4)
        .map((action) => ({ rowKind: 'static' as const, action }));
    }

    // Abaixo de MIN_QUERY_LENGTH o casamento difuso fica ruidoso demais (ex.:
    // "j" bateria com a palavra-chave "novo pj") — mesmo limiar da busca de
    // entidades, para o usuário não ver ações aparecendo/sumindo de forma
    // inconsistente entre as duas seções da paleta.
    if (trimmedQuery.length < MIN_QUERY_LENGTH) return [];

    const scoredContextual = contextualActions
      .map((action) => ({ action, score: scoreAgainst(trimmedQuery, action.label, action.keywords) }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((entry) => ({ rowKind: 'contextual' as const, action: entry.action }));

    const scoredStatic = roleFilteredActions
      .map((action) => ({ action, score: scoreAgainst(trimmedQuery, action.label, action.keywords) }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((entry) => ({ rowKind: 'static' as const, action: entry.action }));

    return [...scoredContextual, ...scoredStatic].slice(0, MAX_ACTION_ROWS);
  }, [mode, hasQuery, trimmedQuery, contextualActions, roleFilteredActions]);

  const navRows = useMemo<FlatRow[]>(() => {
    if (mode !== 'palette' || hasQuery) return [];
    return NAV_GROUPS.flatMap((group) => group.items)
      .filter((item) => item.href !== pathname)
      .filter((item) => {
        const allowed = ROUTE_ROLES[item.href];
        return !allowed || !user || allowed.includes(user.role);
      })
      .slice(0, MAX_NAV_ROWS)
      .map((item) => ({ rowKind: 'nav' as const, item }));
  }, [mode, hasQuery, pathname, user]);

  const flatRows = useMemo<FlatRow[]>(
    () => [...actionRows, ...navRows, ...entityRows],
    [actionRows, navRows, entityRows],
  );
  const totalRows = flatRows.length;

  const executeContextual = useCallback(async (action: ContextualAction) => {
    if (action.manualFeedback) {
      setConfirmAction(null);
      setOpen(false);
      void action.run();
      return;
    }
    try {
      const maybePromise = action.run();
      if (maybePromise && typeof (maybePromise as Promise<void>).then === 'function') {
        setConfirmLoading(true);
        await maybePromise;
        setConfirmLoading(false);
        setConfirmAction(null);
        setFeedback({ type: 'success', message: `✓ ${action.label} concluído(a) com sucesso.` });
      } else {
        setConfirmAction(null);
        setOpen(false);
      }
    } catch (err) {
      setConfirmLoading(false);
      setFeedback({
        type: 'error',
        message: err instanceof Error ? err.message : `Não foi possível concluir "${action.label}".`,
        retry: () => void executeContextual(action),
      });
    }
  }, []);

  const selectRow = useCallback((row: FlatRow) => {
    if (row.rowKind === 'result') {
      const href = mode === 'pick-record' && pendingAction
        ? `${buildSearchResultHref(row.item)}&assistantAction=${pendingAction.id}`
        : buildSearchResultHref(row.item);
      setOpen(false);
      router.push(href);
      return;
    }
    if (row.rowKind === 'nav') {
      setOpen(false);
      router.push(row.item.href);
      return;
    }
    if (row.rowKind === 'static') {
      const action = row.action;
      if (action.kind === 'pick-record') {
        setMode('pick-record');
        setPendingAction(action);
        setQuery('');
        return;
      }
      setOpen(false);
      router.push(buildActionHref(action, paletteCtx));
      return;
    }
    // contextual
    const action = row.action;
    if (action.confirm) {
      setConfirmAction(action);
      return;
    }
    void executeContextual(action);
  }, [mode, pendingAction, paletteCtx, router, executeContextual]);

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (totalRows > 0) setActiveIndex((i) => (i + 1) % totalRows);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (totalRows > 0) setActiveIndex((i) => (i - 1 + totalRows) % totalRows);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const row = flatRows[activeIndex];
      if (row) selectRow(row);
    }
  }

  const showPrompt = !loading && !error && !result && hasQuery && trimmedQuery.length < MIN_QUERY_LENGTH;
  const showEmptyState = !loading && !error && result && entityResultsCount === 0;

  const inputPlaceholder = mode === 'pick-record' && pendingAction?.pickEntity
    ? `Selecione ${ENTITY_ARTICLE_LABEL[pendingAction.pickEntity]}…`
    : 'Buscar cliente, veículo, placa, IMEI, CPF/CNPJ ou uma ação…';

  let rowCursor = -1;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-9 shrink-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-2 text-sm text-slate-500 transition hover:border-slate-300 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200 sm:w-64 sm:px-3"
        aria-label="Busca global"
      >
        <Search className="h-4 w-4 shrink-0" />
        <span className="hidden flex-1 truncate text-left sm:inline">Buscar ou fazer algo…</span>
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
                placeholder={inputPlaceholder}
                autoComplete="off"
                className="flex-1 border-0 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400 dark:text-white dark:placeholder:text-slate-500"
              />
              {(loading || confirmLoading) && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" />}
              <kbd className="hidden shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-3xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400 sm:inline-block">
                Esc
              </kbd>
            </div>

            <div className="flex-1 overflow-y-auto p-2">
              {feedback && (
                <div
                  className={clsx(
                    'mb-2 flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-sm',
                    feedback.type === 'success'
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300'
                      : 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300',
                  )}
                >
                  <span className="flex items-center gap-2">
                    {feedback.type === 'success' ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0" />
                    ) : (
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                    )}
                    {feedback.message}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {feedback.retry && (
                      <button type="button" onClick={feedback.retry} className="font-semibold underline underline-offset-2">
                        Tentar novamente
                      </button>
                    )}
                    <button type="button" onClick={() => setFeedback(null)} aria-label="Fechar aviso">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </span>
                </div>
              )}

              {mode === 'pick-record' && pendingAction && (
                <p className="px-3 pb-2 pt-1 text-xs text-slate-500 dark:text-slate-400">
                  Escolha {ENTITY_ARTICLE_LABEL[pendingAction.pickEntity!]} para &quot;{pendingAction.label}&quot;.
                </p>
              )}

              {actionRows.length > 0 && (
                <div className="mb-2">
                  <PaletteSectionHeader title={actionSectionLabel} />
                  <div className="space-y-0.5">
                    {actionRows.map((row) => {
                      rowCursor += 1;
                      const rowIndex = rowCursor;
                      const active = rowIndex === activeIndex;
                      const action = row.rowKind === 'static' || row.rowKind === 'contextual' ? row.action : null;
                      if (!action) return null;
                      const key = row.rowKind === 'static' ? `static-${action.id}` : `contextual-${action.id}`;
                      return (
                        <PaletteActionRow
                          key={key}
                          label={action.label}
                          icon={action.icon}
                          active={active}
                          onHover={() => setActiveIndex(rowIndex)}
                          onSelect={() => selectRow(row)}
                        />
                      );
                    })}
                  </div>
                </div>
              )}

              {navRows.length > 0 && (
                <div className="mb-2">
                  <PaletteSectionHeader title="Navegação" />
                  <div className="space-y-0.5">
                    {navRows.map((row) => {
                      rowCursor += 1;
                      const rowIndex = rowCursor;
                      const active = rowIndex === activeIndex;
                      if (row.rowKind !== 'nav') return null;
                      return (
                        <PaletteActionRow
                          key={`nav-${row.item.href}`}
                          label={row.item.label}
                          icon={row.item.icon}
                          active={active}
                          onHover={() => setActiveIndex(rowIndex)}
                          onSelect={() => selectRow(row)}
                        />
                      );
                    })}
                  </div>
                </div>
              )}

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

              {result && entityResultsCount > 0 && visibleCategoryOrder.map((key) => {
                const items = result[key];
                if (items.length === 0) return null;
                const entity = items[0].entity;
                const meta = CATEGORY_META[entity];
                const Icon = meta.icon;
                return (
                  <div key={key} className="mb-2 last:mb-0">
                    <PaletteSectionHeader title={meta.label} />
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
                            onClick={() => selectRow({ rowKind: 'result', item })}
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

      {confirmAction?.confirm && (
        <ConfirmDialog
          open
          onClose={() => setConfirmAction(null)}
          onConfirm={() => void executeContextual(confirmAction)}
          title={confirmAction.confirm.title}
          description={confirmAction.confirm.description}
          confirmLabel={confirmAction.confirm.confirmLabel}
          danger={confirmAction.confirm.danger}
          loading={confirmLoading}
        />
      )}
    </>
  );
}
