import { forwardRef, SelectHTMLAttributes } from 'react';
import clsx from 'clsx';

import { larguraPadrao } from '@/lib/tw';

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={clsx(
        larguraPadrao(className),
        'rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition',
        'focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20',
        'disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400',
        'dark:border-slate-700 dark:bg-slate-900 dark:text-white',
        'dark:focus:border-brand-400 dark:focus:ring-brand-400/20',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
);
Select.displayName = 'Select';
