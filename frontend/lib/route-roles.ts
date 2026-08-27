import type { UserRole } from './domain-types';

/**
 * Roles autorizados por rota — fonte única usada tanto pelo useAuthGuard de
 * cada página quanto pelo filtro do menu lateral (Sidebar). Antes cada página
 * tinha sua própria lista literal e a Sidebar não filtrava nada; mudar quem
 * acessa uma tela exigia lembrar de mexer nos dois lugares (ou só um deles).
 *
 * Isto espelha a autorização real, que é sempre feita pelo backend via
 * require_roles (ver app/core/permissions.py) — errar aqui não abre acesso a
 * nada. O pior caso é a tela aparecer/sumir do menu errado, ou um usuário sem
 * permissão levar 403 ao clicar (useAuthGuard já trata isso sem deslogar).
 */
export const ROUTE_ROLES: Record<string, UserRole[]> = {
  '/dashboard': ['admin', 'operacional', 'financeiro'],
  '/clientes': ['admin', 'operacional', 'financeiro'],
  '/veiculos': ['admin', 'operacional', 'financeiro'],
  '/rastreadores': ['admin', 'operacional', 'financeiro'],
  '/ordens-servico': ['admin', 'operacional', 'financeiro'],
  '/integracao': ['admin', 'operacional', 'financeiro'],
  '/financeiro': ['admin', 'financeiro'],
  '/fechamento': ['admin', 'financeiro'],
  '/notas-fiscais': ['admin', 'financeiro'],
  '/relatorios': ['admin', 'financeiro'],
  '/usuarios': ['admin'],
  '/auditoria': ['admin'],
  '/configuracoes': ['admin'],
};
