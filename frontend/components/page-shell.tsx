import { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';

export function PageShell({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-x-hidden">
        {/* Header */}
        <header className="sticky top-0 z-10 flex h-14 items-center border-b border-slate-100 bg-white/90 px-6 backdrop-blur-sm dark:border-slate-800 dark:bg-slate-950/90">
          <div className="flex flex-1 items-center justify-between gap-4">
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold text-slate-900 dark:text-white">
                {title}
              </h1>
              {description && (
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{description}</p>
              )}
            </div>
            {actions && (
              <div className="flex shrink-0 items-center gap-2">{actions}</div>
            )}
          </div>
        </header>

        {/* Content */}
        <div className="mx-auto max-w-screen-xl px-6 py-6">
          {children}
        </div>
      </main>
    </div>
  );
}
