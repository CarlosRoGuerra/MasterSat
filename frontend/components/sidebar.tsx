'use client';

import Link from 'next/link';

import { clearSession } from '@/lib/auth';

const items = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/clientes', label: 'Clientes' },
  { href: '/veiculos', label: 'Veículos' },
];

export function Sidebar() {
  return (
    <aside className="flex min-h-screen w-64 flex-col border-r border-slate-200 bg-brand-900 p-6 text-white">
      <div className="mb-8">
        <p className="text-xs uppercase tracking-[0.2em] text-slate-300">Guerra IT</p>
        <h1 className="text-xl font-bold">Rastreamento ERP</h1>
        <p className="mt-2 text-xs text-slate-300">Sprint atual: clientes, veículos e documentos.</p>
      </div>
      <nav className="space-y-2">
        {items.map((item) => (
          <Link key={item.href} href={item.href} className="block rounded-xl px-3 py-2 text-sm text-slate-100 hover:bg-white/10">
            {item.label}
          </Link>
        ))}
      </nav>
      <button
        onClick={() => {
          clearSession();
          window.location.href = '/login/admin';
        }}
        className="mt-auto rounded-xl border border-white/15 px-3 py-2 text-left text-sm font-semibold text-slate-100 hover:bg-white/10"
      >
        Sair
      </button>
    </aside>
  );
}
