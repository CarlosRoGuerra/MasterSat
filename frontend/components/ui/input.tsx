import { forwardRef, InputHTMLAttributes, TextareaHTMLAttributes } from 'react';
import clsx from 'clsx';

const base =
  'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition ' +
  'placeholder:text-slate-400 ' +
  'focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 ' +
  'disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400 ' +
  'dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 ' +
  'dark:focus:border-brand-400 dark:focus:ring-brand-400/20 ' +
  'dark:disabled:bg-slate-900/50';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={clsx(base, className)} {...props} />
  ),
);
Input.displayName = 'Input';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={clsx(base, 'min-h-[88px] resize-y', className)} {...props} />
  ),
);
Textarea.displayName = 'Textarea';
