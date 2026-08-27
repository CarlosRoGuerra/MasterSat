'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
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
  PlugZap,
  ScrollText,
  BarChart2,
  CalendarCheck,
  Receipt,
  ChevronLeft,
  ChevronRight,
  Settings,
  X,
} from 'lucide-react';

import { logout } from '@/lib/api';
import { ThemeToggle } from '@/components/theme-toggle';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { useCurrentUser } from '@/lib/use-current-user';

type NavItem = { href: string; label: string; icon: React.ComponentType<{ className?: string }> };
type NavGroup = { label: string; items: NavItem[] };

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Visão geral',
    items: [
      { href: '/dashboard',  label: 'Dashboard', icon: LayoutDashboard },
    ],
  },
  {
    label: 'Gestão',
    items: [
      { href: '/clientes',     label: 'Clientes',     icon: Users },
      { href: '/veiculos',     label: 'Veículos',     icon: Car },
      { href: '/rastreadores', label: 'Rastreadores', icon: Radio },
    ],
  },
  {
    label: 'Operações',
    items: [
      { href: '/ordens-servico', label: 'Ordens de serviço', icon: ClipboardList },
      { href: '/financeiro',     label: 'Financeiro',        icon: Wallet },
      { href: '/fechamento',     label: 'Fechamento',        icon: CalendarCheck },
      { href: '/notas-fiscais',  label: 'Notas Fiscais',     icon: Receipt },
    ],
  },
  {
    label: 'Equipe & Config',
    items: [
      { href: '/usuarios',      label: 'Equipe',        icon: ShieldCheck },
      { href: '/relatorios',    label: 'Relatórios',    icon: BarChart2 },
      { href: '/integracao',    label: 'Integração',    icon: PlugZap },
      { href: '/auditoria',     label: 'Auditoria',     icon: ScrollText },
      { href: '/configuracoes', label: 'Configurações', icon: Settings },
    ],
  },
];

function NavTooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="group relative flex">
      {children}
      <div
        className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100 dark:bg-slate-700"
        role="tooltip"
      >
        {label}
        <div className="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-slate-900 dark:border-r-slate-700" />
      </div>
    </div>
  );
}

export function Sidebar({
  mobileOpen = false,
  onMobileClose,
}: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const { data: user } = useCurrentUser();
  // O modo "compacto" (só ícone + tooltip no hover) é um recurso de desktop —
  // no drawer mobile o tooltip não funciona no toque, então força expandido
  // sempre que o drawer está aberto, mesmo com collapsed=true salvo do desktop.
  const showCompact = collapsed && !mobileOpen;

  // Enquanto o role ainda não chegou (ou não achamos restrição para a rota),
  // o item fica visível — quem realmente barra é o backend (require_roles);
  // aqui é só organização de menu, então o lado seguro é mostrar de mais, não
  // esconder uma tela que o usuário tem acesso.
  const visibleGroups = useMemo(() => {
    return NAV_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const allowed = ROUTE_ROLES[item.href];
        return !allowed || !user || allowed.includes(user.role);
      }),
    })).filter((group) => group.items.length > 0);
  }, [user]);

  // Persist collapsed state
  useEffect(() => {
    const stored = localStorage.getItem('sidebar-collapsed');
    if (stored === 'true') setCollapsed(true);
  }, []);

  function toggle() {
    setCollapsed((prev) => {
      localStorage.setItem('sidebar-collapsed', String(!prev));
      return !prev;
    });
  }

  return (
    <>
      {/* Backdrop — mobile only, closes the drawer on tap outside */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-sm lg:hidden"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-50 flex w-[240px] shrink-0 flex-col border-r border-slate-100 bg-white dark:border-slate-800 dark:bg-slate-950',
          'transition-transform duration-200 ease-in-out',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          'lg:static lg:z-auto lg:flex lg:translate-x-0 lg:transition-[width]',
          collapsed ? 'lg:w-[52px]' : 'lg:w-[240px]',
        )}
      >
      {/* Brand */}
      <div className={clsx(
        'flex h-14 items-center border-b border-slate-100 dark:border-slate-800',
        collapsed ? 'px-4 lg:justify-center lg:px-0' : 'px-4',
      )}>
        {collapsed ? (
          <>
            {/* Full logo on mobile even when "collapsed" (desktop-only concept) */}
            <img
              src="/logo.png"
              alt="MasterSat"
              className="h-9 w-auto object-contain lg:hidden"
              draggable={false}
            />
            {/* Icon mark — yellow square with M when collapsed on desktop */}
            <div className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-500 lg:flex">
              <span className="text-sm font-black leading-none text-black">M</span>
            </div>
          </>
        ) : (
          /* Full logo image — place the logo file at frontend/public/logo.png */
          <img
            src="/logo.png"
            alt="MasterSat"
            className="h-9 w-auto object-contain"
            draggable={false}
          />
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {!showCompact && <ThemeToggle />}
          <button
            type="button"
            onClick={onMobileClose}
            aria-label="Fechar menu"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200 lg:hidden"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-3">
        <div className="space-y-4">
          {visibleGroups.map((group, gi) => (
            <div key={group.label}>
              {/* Group separator + label (expanded only) */}
              {gi > 0 && !showCompact && (
                <div className="mb-2 mt-1 border-t border-slate-100 dark:border-slate-800" />
              )}
              {!showCompact && (
                <p className="mb-1 px-3 text-3xs font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-600">
                  {group.label}
                </p>
              )}
              {gi > 0 && showCompact && (
                <div className="mb-1 border-t border-slate-100 dark:border-slate-800" />
              )}

              <div className="space-y-0.5">
                {group.items.map(({ href, label, icon: Icon }) => {
                  const active = pathname === href;
                  const linkContent = (
                    <Link
                      href={href}
                      onClick={onMobileClose}
                      className={clsx(
                        'flex items-center rounded-xl text-sm font-medium transition-colors',
                        showCompact
                          ? 'h-9 w-9 justify-center'
                          : 'gap-3 pl-3 pr-3 py-2',
                        active
                          ? 'bg-brand-500/10 text-brand-700 dark:bg-brand-500/20 dark:text-brand-300'
                          : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-white',
                      )}
                      style={active && !showCompact ? { borderLeft: '3px solid #F0A500', paddingLeft: '10px' } : undefined}
                    >
                      <Icon
                        className={clsx(
                          'h-4 w-4 shrink-0',
                          active ? 'text-brand-700 dark:text-brand-400' : 'text-slate-500 dark:text-slate-400',
                        )}
                      />
                      {!showCompact && <span className={active ? 'font-semibold' : ''}>{label}</span>}
                    </Link>
                  );

                  return showCompact ? (
                    <NavTooltip key={href} label={label}>
                      {linkContent}
                    </NavTooltip>
                  ) : (
                    <div key={href}>{linkContent}</div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-100 px-2 pb-2 pt-3 dark:border-slate-800">
        {showCompact ? (
          <NavTooltip label="Sair">
            <button
              onClick={() => { logout('/login/admin'); }}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:text-slate-400 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
            >
              <LogOut className="h-4 w-4 shrink-0" />
            </button>
          </NavTooltip>
        ) : (
          <button
            onClick={() => { logout('/login/admin'); }}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-slate-500 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:text-slate-400 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
          >
            <LogOut className="h-4 w-4 shrink-0" />
            Sair
          </button>
        )}

        {/* Collapse toggle — recurso de desktop, não faz sentido no drawer mobile */}
        <button
          onClick={toggle}
          className={clsx(
            'mt-1 hidden items-center rounded-xl text-xs font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-400 lg:flex',
            collapsed ? 'h-9 w-9 justify-center' : 'w-full gap-2 px-3 py-2',
          )}
          title={collapsed ? 'Expandir menu' : 'Recolher menu'}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <>
              <ChevronLeft className="h-4 w-4" />
              <span>Recolher</span>
            </>
          )}
        </button>
      </div>
      </aside>
    </>
  );
}
