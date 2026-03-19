import { ButtonHTMLAttributes } from 'react';
import clsx from 'clsx';
export function Button({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) { return <button className={clsx('rounded-xl bg-brand-500 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60', className)} {...props} />; }
