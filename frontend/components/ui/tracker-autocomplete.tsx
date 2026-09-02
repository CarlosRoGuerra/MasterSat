'use client';

import { useEffect, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import clsx from 'clsx';

/**
 * Propositalmente NÃO é `Pick<TrackerOption, ...>` de lib/domain-types: lá
 * `imei` é `string` obrigatório (reflete o schema OpenAPI), mas este
 * componente é usado com rastreadores que ainda podem não ter IMEI lido —
 * ex.: app/integracao (Tracker local) e outros consumidores que modelam
 * `imei` como opcional. Unificar os dois tipos quebraria essa distinção real.
 */
type TrackerOption = {
  id: number;
  imei?: string | null;
  brand?: string | null;
  model?: string | null;
};

export function TrackerAutocomplete({
  trackers,
  value,
  onChange,
  placeholder = 'Buscar por IMEI, marca ou modelo…',
  disabled,
}: {
  trackers: TrackerOption[];
  value: number | string;
  onChange: (id: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = trackers.find((t) => String(t.id) === String(value));

  // When value changes externally (e.g. form reset), sync query
  useEffect(() => {
    if (!value) setQuery('');
  }, [value]);

  // Close on outside click
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setFocused(false);
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const filtered = query.trim()
    ? trackers.filter((t) => {
        const q = query.trim().toLowerCase();
        return (
          (t.imei ?? '').toLowerCase().includes(q) ||
          (t.brand ?? '').toLowerCase().includes(q) ||
          (t.model ?? '').toLowerCase().includes(q)
        );
      })
    : trackers;

  function selectTracker(tracker: TrackerOption) {
    onChange(String(tracker.id));
    setQuery('');
    setOpen(false);
    setFocused(false);
  }

  function clearSelection() {
    onChange('');
    setQuery('');
    inputRef.current?.focus();
  }

  const showDropdown = open && focused;

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Selected tracker chip */}
      {selected && !focused ? (
        <div className="flex items-center gap-2 rounded-xl border border-brand-300 bg-brand-50 px-3.5 py-2.5 dark:border-brand-700 dark:bg-brand-950/30">
          <span className="flex-1 truncate font-mono text-sm font-medium text-brand-800 dark:text-brand-200">
            {selected.imei || 'Sem IMEI'}
          </span>
          {(selected.brand || selected.model) && (
            <span className="shrink-0 text-xs text-brand-500 dark:text-brand-400">
              {[selected.brand, selected.model].filter(Boolean).join(' ')}
            </span>
          )}
          {!disabled && (
            <button
              type="button"
              onClick={clearSelection}
              className="ml-1 rounded p-0.5 text-brand-500 hover:bg-brand-100 hover:text-brand-700 dark:hover:bg-brand-900/60"
              aria-label="Remover seleção"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      ) : (
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            disabled={disabled}
            placeholder={selected ? (selected.imei || 'Sem IMEI') : placeholder}
            autoComplete="off"
            onFocus={() => { setFocused(true); setOpen(true); }}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onBlur={() => {
              // Delay to allow click on option
              setTimeout(() => {
                if (!containerRef.current?.contains(document.activeElement)) {
                  setOpen(false);
                  setFocused(false);
                }
              }, 150);
            }}
            className={clsx(
              'w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3.5 text-sm text-slate-900 outline-none transition',
              'placeholder:text-slate-500',
              'focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20',
              'disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500',
              'dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-400',
              'dark:focus:border-brand-400 dark:focus:ring-brand-400/20',
            )}
          />
        </div>
      )}

      {/* Dropdown */}
      {showDropdown && (
        <div className="absolute z-50 mt-1 max-h-60 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-elevated dark:border-slate-700 dark:bg-slate-900">
          {filtered.length === 0 ? (
            <div className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">
              Nenhum equipamento encontrado para &quot;{query}&quot;
            </div>
          ) : (
            filtered.slice(0, 20).map((tracker) => (
              <button
                key={tracker.id}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); selectTracker(tracker); }}
                className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
              >
                <span className="font-mono font-medium text-slate-900 dark:text-white">
                  {highlight(tracker.imei || 'Sem IMEI', query)}
                </span>
                {(tracker.brand || tracker.model) && (
                  <span className="shrink-0 text-xs text-slate-500">
                    {[tracker.brand, tracker.model].filter(Boolean).join(' ')}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function highlight(text: string, query: string) {
  if (!query.trim()) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase().trim());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-brand-100 text-brand-800 dark:bg-brand-900/60 dark:text-brand-200">
        {text.slice(idx, idx + query.trim().length)}
      </mark>
      {text.slice(idx + query.trim().length)}
    </>
  );
}
