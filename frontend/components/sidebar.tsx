'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import clsx from 'clsx';
import {
  LayoutDashboard,
  Users,
  Car,
  Radio,
  ClipboardList,
  Wallet,
  ShieldCheck,
  LogOut,
  Satellite,
  PlugZap,
  ScrollText,
} from 'lucide-react';

import { clearSession } from '@/lib/auth';
import { ThemeToggle } from '@/components/theme-toggle';

const items = [
  { href: '/dashboard',      label: 'Dashboard',          icon: LayoutDashboard },
  { href: '/clientes',       label: 'Clientes',            icon: Users },
  { href: '/veiculos',       label: 'Veículos',            icon: Car },
  { href: '/rastreadores',   label: 'Rastreadores',        icon: Radio },
  { href: '/ordens-servico', label: 'Ordens de serviço',   icon: ClipboardList },
  { href: '/financeiro',     label: 'Financeiro',          icon: Wallet },
  { href: '/usuarios',       label: 'Equipe',              icon: ShieldCheck },
  { href: '/integracao',     label: 'Integração',          icon: PlugZap },
  { href: '/auditoria',      label: 'Auditoria',           icon: ScrollText },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden min-h-screen w-[240px] shrink-0 flex-col border-r border-slate-100 bg-white lg:flex dark:border-slate-800 dark:bg-slate-950">
      {/* Brand */}
      <div className="flex h-14 items-center gap-3 border-b border-slate-100 px-4 dark:border-slate-800">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-700">
          <Satellite className="h-4 w-4 text-white" />
        </div>
        <span className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">
          Mastersat
        </span>
        <ThemeToggle className="ml-auto shrink-0" />
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400 dark:text-slate-600">
          Menu
        </p>
        <div className="space-y-0.5">
          {items.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  'flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors',
                  active
                    ? 'bg-brand-700 text-white'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-white',
                )}
              >
                <Icon
                  className={clsx(
                    'h-4 w-4 shrink-0',
                    active ? 'text-white' : 'text-slate-400 dark:text-slate-500',
                  )}
                />
                {label}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-100 px-2 py-2 dark:border-slate-800">
        <button
          onClick={() => { clearSession(); window.location.href = '/login/admin'; }}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-slate-500 transition-colors hover:bg-red-50 hover:text-red-600 dark:text-slate-500 dark:hover:bg-red-950/40 dark:hover:text-red-400"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          Sair
        </button>
      </div>
    </aside>
  );
}
