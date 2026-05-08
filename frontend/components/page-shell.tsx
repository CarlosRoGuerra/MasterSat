import { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';

export function PageShell({
  title,
  children,
  description,
  actions,
}: {
  title: string;
  children: ReactNode;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-x-hidden">
        {/* Page header */}
        <div className="border-b border-slate-200 bg-white px-6 py-5 dark:border-slate-800/80 dark:bg-slate-950">
          <div className="mx-auto flex max-w-screen-2xl items-center justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold text-slate-900 dark:text-white">{title}</h1>
              {description && (
                <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{description}</p>
              )}
            </div>
            {actions && <div className="flex items-center gap-2">{actions}</div>}
          </div>
        </div>

        {/* Page content */}
        <div className="mx-auto max-w-screen-2xl px-6 py-6">
          {children}
        </div>
      </main>
    </div>
  );
}
