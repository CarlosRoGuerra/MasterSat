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
} from 'lucide-react';

import { clearSession } from '@/lib/auth';
import { ThemeToggle } from '@/components/theme-toggle';

const items = [
  { href: '/dashboard',      label: 'Dashboard',            description: 'Resumo executivo',                  icon: LayoutDashboard },
  { href: '/clientes',       label: 'Clientes',             description: 'Base cadastral e documentos',       icon: Users },
  { href: '/veiculos',       label: 'Veículos',             description: 'Ativos e controle de instalação',   icon: Car },
  { href: '/rastreadores',   label: 'Rastreadores',         description: 'Equipamentos e vínculos',           icon: Radio },
  { href: '/ordens-servico', label: 'Ordens de serviço',    description: 'Operação em campo',                 icon: ClipboardList },
  { href: '/financeiro',     label: 'Financeiro',           description: 'Planos, contratos e cobranças',     icon: Wallet },
  { href: '/usuarios',       label: 'Equipe administrativa', description: 'Perfis e acessos',                 icon: ShieldCheck },
  { href: '/integracao',     label: 'Integração Multiportal', description: 'Sincronização com plataforma',    icon: PlugZap },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden min-h-screen w-[280px] shrink-0 flex-col border-r border-slate-200 bg-white lg:flex dark:border-slate-800/80 dark:bg-slate-950">
      {/* Brand header */}
      <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800/80">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-700">
          <Satellite className="h-5 w-5 text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">Mastersat</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">Gestão de rastreamento</p>
        </div>
        <ThemeToggle className="shrink-0" />
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400 dark:text-slate-600">
          Menu
        </p>
        <div className="space-y-0.5">
          {items.map((item) => {
            const isActive = pathname === item.href;
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  'flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors',
                  isActive
                    ? 'bg-brand-700 text-white'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white',
                )}
              >
                <Icon className={clsx('h-[18px] w-[18px] shrink-0', isActive ? 'text-white' : 'text-slate-400 dark:text-slate-500')} />
                <div className="min-w-0">
                  <p className="text-sm font-medium leading-none">{item.label}</p>
                  <p className={clsx('mt-0.5 truncate text-xs', isActive ? 'text-white/70' : 'text-slate-400 dark:text-slate-600')}>{item.description}</p>
                </div>
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-100 px-3 py-3 dark:border-slate-800/80">
        <button
          onClick={() => { clearSession(); window.location.href = '/login/admin'; }}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-slate-500 transition-colors hover:bg-red-50 hover:text-red-600 dark:text-slate-500 dark:hover:bg-red-950/40 dark:hover:text-red-400"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          Encerrar sessão
        </button>
      </div>
    </aside>
  );
}
