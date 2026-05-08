import { ButtonHTMLAttributes } from 'react';
import clsx from 'clsx';

export function Button({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center rounded-2xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-slate-900/10 transition hover:-translate-y-0.5 hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 dark:bg-cyan-400 dark:text-slate-950 dark:shadow-cyan-950/20 dark:hover:bg-cyan-300',
        className,
      )}
      {...props}
    />
  );
}
