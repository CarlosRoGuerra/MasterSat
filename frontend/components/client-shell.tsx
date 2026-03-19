'use client';

import Link from 'next/link';
import { ReactNode } from 'react';

import { clearSession } from '@/lib/auth';

export function ClientShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-brand-500">Portal do Cliente</p>
            <h1 className="text-xl font-bold text-slate-900">Rastreamento Veicular</h1>
          </div>
          <nav className="flex items-center gap-3 text-sm">
            <Link href="/cliente/dashboard" className="rounded-xl px-3 py-2 text-slate-700 hover:bg-slate-100">
              Dashboard
            </Link>
            <button
              onClick={() => {
                clearSession();
                window.location.href = '/login/cliente';
              }}
              className="rounded-xl border border-slate-300 px-3 py-2 font-semibold text-slate-700"
            >
              Sair
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6">
          <h2 className="text-3xl font-bold text-slate-900">{title}</h2>
          <p className="text-sm text-slate-500">Acompanhe seu cadastro, veículos vinculados e cobranças do contrato.</p>
        </div>
        {children}
      </main>
    </div>
  );
}
