'use client';

import { useEffect, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import clsx from 'clsx';

type ClientOption = {
  id: number;
  name: string;
  cpf_cnpj?: string;
};

export function ClientAutocomplete({
  clients,
  value,
  onChange,
  placeholder = 'Buscar por nome ou CPF/CNPJ…',
  required,
  disabled,
}: {
  clients: ClientOption[];
  value: number | string;
  onChange: (id: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = clients.find((c) => String(c.id) === String(value));

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
    ? clients.filter((c) => {
        const q = query.trim().toLowerCase();
        const qDigits = q.replace(/\D/g, '');
        return (
          c.name.toLowerCase().includes(q) ||
          // "".includes("") é sempre true — sem o length>0, buscar um texto
          // sem nenhum dígito (ex.: "zzz") batia com QUALQUER cliente aqui.
          (qDigits.length > 0 && (c.cpf_cnpj ?? '').replace(/\D/g, '').includes(qDigits))
        );
      })
    : [];

  function selectClient(client: ClientOption) {
    onChange(String(client.id));
    setQuery('');
    setOpen(false);
    setFocused(false);
  }

  function clearSelection() {
    onChange('');
    setQuery('');
    inputRef.current?.focus();
  }

  const showDropdown = open && focused && query.trim().length >= 1;

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Selected client chip */}
      {selected && !focused ? (
        <div className="flex items-center gap-2 rounded-xl border border-brand-300 bg-brand-50 px-3.5 py-2.5 dark:border-brand-700 dark:bg-brand-950/30">
          <span className="flex-1 text-sm font-medium text-brand-800 dark:text-brand-200">
            {selected.name}
          </span>
          <span className="text-xs text-brand-500 dark:text-brand-400">
            {formatDoc(selected.cpf_cnpj)}
          </span>
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
            required={required && !value}
            placeholder={selected ? selected.name : placeholder}
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
              Nenhum cliente encontrado para &quot;{query}&quot;
            </div>
          ) : (
            filtered.slice(0, 20).map((client) => (
              <button
                key={client.id}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); selectClient(client); }}
                className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
              >
                <span className="font-medium text-slate-900 dark:text-white">
                  {highlight(client.name, query)}
                </span>
                <span className="shrink-0 text-xs text-slate-500">
                  {formatDoc(client.cpf_cnpj)}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function formatDoc(doc?: string) {
  if (!doc) return '';
  const d = doc.replace(/\D/g, '');
  if (d.length === 11) return d.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, '$1.$2.$3-$4');
  if (d.length === 14) return d.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, '$1.$2.$3/$4-$5');
  return doc;
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
