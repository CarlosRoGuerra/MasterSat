import { ReactElement, ReactNode, cloneElement, isValidElement, useId } from 'react';
import clsx from 'clsx';

/**
 * Único filho aceito pelo clone de id: qualquer elemento cujas props possam
 * receber `id`/`aria-*` (Input, Select, Textarea e o `<input>` nativo já
 * cobrem 100% do uso atual). Filhos compostos (ex.: um `<div>` com vários
 * campos dentro) simplesmente recebem o id no elemento raiz — inofensivo,
 * mas não associa cada subcampo individualmente.
 */
type FieldElement = ReactElement<{ id?: string; 'aria-invalid'?: boolean; 'aria-describedby'?: string }>;

export function FormField({
  label,
  hint,
  error,
  required,
  children,
  className,
  id: idProp,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
  className?: string;
  /** Sobrescreve o id gerado automaticamente — usar quando outro elemento (ex.: label externo) já referencia um id fixo. */
  id?: string;
}) {
  const generatedId = useId();
  // Se o filho já traz um id próprio, o label deve apontar pra ele — não o
  // contrário — senão label e input ficam associados a ids diferentes.
  const childId = isValidElement(children) ? (children as FieldElement).props.id : undefined;
  const fieldId = childId ?? idProp ?? generatedId;
  const errorId = error ? `${fieldId}-error` : undefined;

  const child = isValidElement(children)
    ? cloneElement(children as FieldElement, {
        id: fieldId,
        ...(error ? { 'aria-invalid': true, 'aria-describedby': errorId } : {}),
      })
    : children;

  return (
    <div className={clsx('flex flex-col gap-1.5', className)}>
      <label htmlFor={fieldId} className="text-xs font-semibold text-slate-600 dark:text-slate-400">
        {label}
        {required && <span className="ml-0.5 text-rose-500">*</span>}
      </label>
      {child}
      {hint && !error && (
        <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-rose-500">{error}</p>
      )}
    </div>
  );
}

export function FormSection({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('space-y-4', className)}>
      <h4 className="text-xs font-bold uppercase tracking-widest text-slate-500 dark:text-slate-600">
        {title}
      </h4>
      {children}
    </div>
  );
}

export function FormGrid({
  cols = 2,
  children,
  className,
}: {
  cols?: 1 | 2 | 3;
  children: ReactNode;
  className?: string;
}) {
  const gridCols = { 1: 'grid-cols-1', 2: 'grid-cols-1 sm:grid-cols-2', 3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3' }[cols];
  return (
    <div className={clsx('grid gap-4', gridCols, className)}>
      {children}
    </div>
  );
}

export function FormDivider() {
  return <hr className="border-slate-100 dark:border-slate-800" />;
}
