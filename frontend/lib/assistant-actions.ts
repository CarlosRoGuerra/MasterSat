import type { LucideIcon } from 'lucide-react';
import {
  UserPlus,
  CarFront,
  RadioTower,
  ClipboardPlus,
  Users,
  Car,
  Radio,
  ClipboardList,
  Wallet,
  PlugZap,
  ArrowRightLeft,
  Signal,
} from 'lucide-react';

import { ROUTE_ROLES } from '@/lib/route-roles';
import type { SearchEntity, UserRole } from '@/lib/domain-types';

export type AssistantActionKind = 'navigate' | 'navigate-with-prefill' | 'pick-record';

/**
 * Contexto da tela atual, usado só para decidir prefill (ex.: "Novo veículo"
 * chamado de dentro de /clientes?focus=42 já entra com o cliente 42). Não é
 * o mesmo mecanismo das ações contextuais registradas via
 * assistant-context.tsx — este aqui é lido puramente da URL corrente, sem
 * nenhuma página precisar publicar nada.
 */
export interface PaletteCtx {
  pathname: string;
  focusClientId?: number;
  focusVehicleId?: number;
}

export interface AssistantActionDef {
  id: string;
  label: string;
  /** Frases/sinônimos usados no casamento difuso — não precisa repetir `label`. */
  keywords: string[];
  icon: LucideIcon;
  category: 'criar' | 'navegacao' | 'consultar';
  /** Espelha o backend (require_roles do endpoint que a ação acaba acionando) — nunca mais permissivo. */
  roles: UserRole[];
  kind: AssistantActionKind;
  route: string;
  prefillParams?: (ctx: PaletteCtx) => Record<string, string>;
  /** Presente só quando kind === 'pick-record' — qual tipo de registro escolher antes de navegar. */
  pickEntity?: SearchEntity;
}

function routeRoles(route: string): UserRole[] {
  return ROUTE_ROLES[route] ?? [];
}

const EDIT_ROLES: UserRole[] = ['admin', 'operacional'];

export const ASSISTANT_ACTIONS: AssistantActionDef[] = [
  {
    id: 'novo-cliente',
    label: 'Novo cliente',
    keywords: ['cliente novo', 'cadastrar cliente', 'criar cliente', 'adicionar cliente', 'novo pf', 'novo pj'],
    icon: UserPlus,
    category: 'criar',
    roles: EDIT_ROLES,
    kind: 'navigate-with-prefill',
    route: '/clientes',
  },
  {
    id: 'novo-veiculo',
    label: 'Novo veículo',
    keywords: ['veiculo novo', 'cadastrar veiculo', 'criar veiculo', 'adicionar veiculo', 'novo carro'],
    icon: CarFront,
    category: 'criar',
    roles: EDIT_ROLES,
    kind: 'navigate-with-prefill',
    route: '/veiculos',
    prefillParams: (ctx) => {
      const params: Record<string, string> = {};
      if (ctx.focusClientId) params.prefillClientId = String(ctx.focusClientId);
      return params;
    },
  },
  {
    id: 'novo-rastreador',
    label: 'Novo rastreador',
    keywords: ['rastreador novo', 'cadastrar rastreador', 'criar rastreador', 'adicionar rastreador', 'novo imei', 'novo gps'],
    icon: RadioTower,
    category: 'criar',
    roles: EDIT_ROLES,
    kind: 'navigate-with-prefill',
    route: '/rastreadores',
    prefillParams: (ctx) => {
      const params: Record<string, string> = {};
      if (ctx.focusVehicleId) params.prefillVehicleId = String(ctx.focusVehicleId);
      else if (ctx.focusClientId) params.prefillClientId = String(ctx.focusClientId);
      return params;
    },
  },
  {
    id: 'nova-os',
    label: 'Nova ordem de serviço',
    keywords: ['nova os', 'criar os', 'abrir os', 'cadastrar os', 'nova ordem de servico'],
    icon: ClipboardPlus,
    category: 'criar',
    roles: EDIT_ROLES,
    kind: 'navigate-with-prefill',
    route: '/ordens-servico',
    prefillParams: (ctx) => ({
      ...(ctx.focusClientId ? { prefillClientId: String(ctx.focusClientId) } : {}),
      ...(ctx.focusVehicleId ? { prefillVehicleId: String(ctx.focusVehicleId) } : {}),
    }),
  },
  {
    id: 'abrir-clientes',
    label: 'Abrir Clientes',
    keywords: ['ir para clientes', 'lista de clientes'],
    icon: Users,
    category: 'navegacao',
    roles: routeRoles('/clientes'),
    kind: 'navigate',
    route: '/clientes',
  },
  {
    id: 'abrir-veiculos',
    label: 'Abrir Veículos',
    keywords: ['ir para veiculos', 'lista de veiculos'],
    icon: Car,
    category: 'navegacao',
    roles: routeRoles('/veiculos'),
    kind: 'navigate',
    route: '/veiculos',
  },
  {
    id: 'abrir-rastreadores',
    label: 'Abrir Rastreadores',
    keywords: ['ir para rastreadores', 'lista de rastreadores'],
    icon: Radio,
    category: 'navegacao',
    roles: routeRoles('/rastreadores'),
    kind: 'navigate',
    route: '/rastreadores',
  },
  {
    id: 'abrir-os',
    label: 'Abrir Ordens de Serviço',
    keywords: ['ir para os', 'lista de os', 'ordens de servico'],
    icon: ClipboardList,
    category: 'navegacao',
    roles: routeRoles('/ordens-servico'),
    kind: 'navigate',
    route: '/ordens-servico',
  },
  {
    id: 'abrir-financeiro',
    label: 'Abrir Financeiro',
    keywords: ['ir para financeiro', 'cobrancas', 'boletos'],
    icon: Wallet,
    category: 'navegacao',
    roles: routeRoles('/financeiro'),
    kind: 'navigate',
    route: '/financeiro',
  },
  {
    id: 'abrir-integracao',
    label: 'Abrir Integração',
    keywords: ['multiportal', 'ir para integracao'],
    icon: PlugZap,
    category: 'navegacao',
    roles: routeRoles('/integracao'),
    kind: 'navigate',
    route: '/integracao',
  },
  {
    id: 'trocar-rastreador',
    label: 'Trocar rastreador',
    keywords: ['substituir rastreador', 'trocar imei', 'trocar equipamento', 'swap rastreador'],
    icon: ArrowRightLeft,
    category: 'consultar',
    roles: EDIT_ROLES,
    kind: 'pick-record',
    route: '/veiculos',
    pickEntity: 'vehicle',
  },
  {
    id: 'consultar-rastreador',
    label: 'Consultar rastreador',
    keywords: ['status do rastreador', 'consultar imei', 'verificar vinculo', 'consultar vinculo'],
    icon: Signal,
    category: 'consultar',
    roles: ['admin', 'operacional', 'financeiro'],
    kind: 'pick-record',
    route: '/rastreadores',
    pickEntity: 'tracker',
  },
];

export function filterActionsByRole(actions: AssistantActionDef[], role: UserRole | undefined): AssistantActionDef[] {
  if (!role) return actions;
  return actions.filter((action) => action.roles.includes(role));
}

/** Monta a URL de navegação para ações `navigate`/`navigate-with-prefill` (não usado para `pick-record`, que depende do registro escolhido). */
export function buildActionHref(action: AssistantActionDef, ctx: PaletteCtx): string {
  if (action.kind === 'navigate') return action.route;

  const params = new URLSearchParams({ assistantAction: 'new' });
  const prefill = action.prefillParams?.(ctx) ?? {};
  for (const [key, value] of Object.entries(prefill)) params.set(key, value);
  return `${action.route}?${params.toString()}`;
}
