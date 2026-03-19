import { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';
export function PageShell({ title, children }: { title: string; children: ReactNode }) { return <div className="flex min-h-screen"><Sidebar /><main className="flex-1 p-8"><div className="mb-6"><h2 className="text-2xl font-bold text-slate-900">{title}</h2><p className="text-sm text-slate-500">Base inicial do sistema de rastreamento veicular.</p></div>{children}</main></div>; }
