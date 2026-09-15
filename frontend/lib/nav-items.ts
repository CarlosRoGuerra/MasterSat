import {
  LayoutDashboard,
  Users,
  Car,
  Radio,
  ClipboardList,
  Wallet,
  ShieldCheck,
  PlugZap,
  ScrollText,
  BarChart2,
  CalendarCheck,
  Receipt,
  Settings,
} from 'lucide-react';

/**
 * Rotas de navegação — fonte única usada pela Sidebar e pela seção
 * "Navegação" do Assistente de Ações (global-search.tsx), para as duas
 * nunca divergirem sobre quais telas existem e como se chamam.
 */
export type NavItem = { href: string; label: string; icon: React.ComponentType<{ className?: string }> };
export type NavGroup = { label: string; items: NavItem[] };

export const NAV_GROUPS: NavGroup[] = [
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
