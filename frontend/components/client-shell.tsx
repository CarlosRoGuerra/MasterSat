'use client';

import Link from 'next/link';
import { ReactNode } from 'react';

import { clearSession } from '@/lib/auth';
import { ThemeToggle } from '@/components/theme-toggle';

export function ClientShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-transparent">
      <header className="border-b border-white/70 bg-white backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 lg:flex-row lg:items-center lg:justify-between lg:px-6">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-brand-500 dark:text-cyan-300">Portal do Cliente</p>
            <h1 className="mt-1 text-2xl font-semibold text-slate-900 dark:text-white">Mastersat Rastreamento</h1>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-300">Acompanhe seus dados cadastrais, documentos e veículos vinculados.</p>
          </div>
          <nav className="flex flex-wrap items-center gap-3 text-sm">
            <ThemeToggle className="!static" />
            <Link href="/cliente/dashboard" className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-slate-100 dark:hover:border-cyan-400 dark:hover:text-cyan-300">
              Dashboard
            </Link>
            <button
              onClick={() => {
                clearSession();
                window.location.href = '/login/admin';
              }}
              className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-950/70 dark:text-slate-100 dark:hover:bg-slate-900"
            >
              Sair
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 lg:px-6 lg:py-8">
        <div className="mb-6 rounded-[30px] border border-white/70 bg-white px-6 py-5 shadow-[0_24px_60px_-38px_rgba(15,23,42,0.35)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/90 dark:shadow-[0_24px_60px_-38px_rgba(2,6,23,0.8)]">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-brand-500 dark:text-cyan-300">Minha área</p>
          <h2 className="mt-3 text-3xl font-semibold text-slate-900 dark:text-white">{title}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-300">
            Visualize seus veículos vinculados, acompanhe seus documentos e mantenha seus dados sempre atualizados.
          </p>
        </div>
        {children}
      </main>
    </div>
  );
}
