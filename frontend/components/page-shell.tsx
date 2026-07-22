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
    <div className="flex h-screen overflow-hidden bg-slate-50 dark:bg-slate-950">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden">
        {/* Header */}
        <header className="sticky top-0 z-10 flex h-14 items-center border-b border-slate-100 bg-white/90 px-6 backdrop-blur-sm dark:border-slate-800 dark:bg-slate-950/90">
          <div className="flex flex-1 items-center justify-between gap-4">
            <div className="min-w-0">
              <h1 className="truncate text-[22px] font-semibold text-slate-900 dark:text-white">
                {title}
              </h1>
              {description && (
                <p className="truncate text-[13px] font-normal text-slate-500 dark:text-slate-400">{description}</p>
              )}
            </div>
            {actions && (
              <div className="flex shrink-0 items-center gap-2">{actions}</div>
            )}
          </div>
        </header>

        {/* Content — largura total (padrão do sistema antigo), sem cap central */}
        <div className="w-full px-6 py-6">
          {children}
        </div>
      </main>
    </div>
  );
}
