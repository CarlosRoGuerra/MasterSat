'use client';

import { ReactNode, useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import clsx from 'clsx';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onClose,
  title,
  description,
  subtitle,
  children,
  size = 'lg',
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  subtitle?: string;
  children: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  footer?: ReactNode;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // onClose costuma ser passado como arrow function inline pelos chamadores
  // (`onClose={() => setXModal(false)}`), então sua identidade muda a cada
  // render do componente pai — inclusive a cada tecla digitada em qualquer
  // campo da modal. Se o efeito abaixo dependesse de `onClose` diretamente,
  // ele re-executaria a cada tecla e o cleanup devolveria o foco ao
  // elemento que abriu a modal no meio da digitação. Por isso a última
  // versão de onClose fica numa ref, e o efeito de foco/teclado depende só
  // de `open` (abre/fecha), não da identidade da função.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    // Guarda o elemento com foco antes de abrir, para devolver o foco a ele
    // ao fechar (senão o teclado "perde o lugar" depois de fechar a modal).
    triggerRef.current = document.activeElement as HTMLElement | null;
    // Foco inicial no próprio contêiner do diálogo — não assume qual é o
    // "primeiro campo certo" em conteúdos tão variados quanto os deste app.
    const focusTimer = window.setTimeout(() => dialogRef.current?.focus(), 0);

    function handler(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab' || !dialogRef.current) return;

      // Focus trap: Tab/Shift+Tab não escapam da modal enquanto ela está aberta.
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => el.offsetParent !== null);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;

      if (e.shiftKey) {
        if (active === first || !dialogRef.current.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !dialogRef.current.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    }
    window.addEventListener('keydown', handler);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', handler);
      window.clearTimeout(focusTimer);
      triggerRef.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  // Larguras aumentadas: as modais com tabela (boletos do cliente, notas)
  // ficavam estreitas e a grade rolava na horizontal.
  const widths = {
    sm: 'max-w-2xl',
    md: 'max-w-4xl',
    lg: 'max-w-5xl',
    xl: 'max-w-7xl',
    '2xl': 'max-w-[95rem]',
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/40 px-4 py-6 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={clsx(
          'flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-elevated',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 dark:border-slate-800 dark:bg-slate-900',
          widths[size],
        )}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-slate-100 px-6 py-4 dark:border-slate-800">
          <div className="min-w-0">
            {subtitle && (
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-brand-500 dark:text-brand-400">
                {subtitle}
              </p>
            )}
            <h3 id={titleId} className="text-lg font-semibold text-slate-900 dark:text-white">{title}</h3>
            {description && (
              <p id={descriptionId} className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Fechar"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="shrink-0 border-t border-slate-100 px-6 py-4 dark:border-slate-800">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
