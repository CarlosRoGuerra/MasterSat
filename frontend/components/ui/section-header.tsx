import { ReactNode } from 'react';

export function SectionHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        {eyebrow && (
          <p className="text-3xs font-bold uppercase tracking-[0.2em] text-brand-500 dark:text-brand-400">
            {eyebrow}
          </p>
        )}
        <h3 className="mt-1 text-lg font-semibold tracking-tight text-slate-900 dark:text-white">
          {title}
        </h3>
        {description && (
          <p className="mt-1 text-sm leading-relaxed text-slate-500 dark:text-slate-400">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
