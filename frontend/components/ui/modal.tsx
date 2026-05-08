'use client';

import { ReactNode, useEffect } from 'react';
import clsx from 'clsx';

export function Modal({ open, onClose, title, description, children, size = 'lg' }: { open: boolean; onClose: () => void; title: string; description?: string; children: ReactNode; size?: 'md' | 'lg' | 'xl' | '2xl'; }) {
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const sizeClass = { md: 'max-w-2xl', lg: 'max-w-4xl', xl: 'max-w-5xl', '2xl': 'max-w-6xl' }[size];

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/50 px-4 py-6 backdrop-blur-sm" onMouseDown={onClose}>
      <div className={clsx('w-full overflow-hidden rounded-[32px] border border-slate-200/80 bg-white/95 shadow-[0_28px_120px_-48px_rgba(15,23,42,0.55)] dark:border-slate-800 dark:bg-slate-900/95', sizeClass)} onMouseDown={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-slate-200/80 px-6 py-5 dark:border-slate-800">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-brand-500 dark:text-cyan-300">Cadastro e edição</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">{title}</h3>
            {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">{description}</p> : null}
          </div>
          <button type="button" onClick={onClose} className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 text-slate-500 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-400 dark:hover:border-cyan-400 dark:hover:text-cyan-300" aria-label="Fechar modal">✕</button>
        </div>
        <div className="max-h-[calc(100vh-9rem)] overflow-y-auto px-6 py-6">{children}</div>
      </div>
    </div>
  );
}
