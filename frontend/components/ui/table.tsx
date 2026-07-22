import { ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react';
import clsx from 'clsx';

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900', className)}>
      <table className="min-w-full text-sm">{children}</table>
    </div>
  );
}

export function TableHead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-slate-100 bg-slate-50/80 dark:border-slate-800 dark:bg-slate-900/60">
      <tr>{children}</tr>
    </thead>
  );
}

export function Th({
  children,
  className,
  ...props
}: { children?: ReactNode } & ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={clsx(
        'px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-widest text-slate-400 dark:text-slate-500',
        className,
      )}
      {...props}
    >
      {children}
    </th>
  );
}

export function TableBody({ children }: { children: ReactNode }) {
  // Zebra: linhas pares em branco, ímpares em amarelo bem claro (facilita
  // acompanhar a linha na horizontal em tabelas largas). `even:` respeita a
  // ordem real das linhas renderizadas; linhas selecionadas têm prioridade.
  return (
    <tbody
      className={clsx(
        'divide-y divide-slate-100 dark:divide-slate-800',
        '[&>tr:nth-child(even)]:bg-amber-50/60 dark:[&>tr:nth-child(even)]:bg-slate-800/40',
      )}
    >
      {children}
    </tbody>
  );
}

export function Tr({
  children,
  onClick,
  className,
  selected,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  selected?: boolean;
}) {
  return (
    <tr
      onClick={onClick}
      className={clsx(
        'transition-colors',
        // `!` para vencer a zebra do tbody (que tem especificidade maior)
        onClick && 'cursor-pointer hover:!bg-slate-100 dark:hover:!bg-slate-800/70',
        selected && '!bg-brand-100/70 dark:!bg-brand-950/40',
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function Td({
  children,
  className,
  ...props
}: { children?: ReactNode } & TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={clsx('px-4 py-3 text-[13px] text-slate-700 dark:text-slate-300', className)}
      {...props}
    >
      {children}
    </td>
  );
}
