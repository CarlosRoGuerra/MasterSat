import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

import { ROUTE_ROLES } from '@/lib/route-roles';
import { SESSION_HINT_COOKIE } from '@/lib/auth';

/**
 * Redireciona para o login ANTES de renderizar, quando não há nem sinal de
 * sessão — elimina o flash de conteúdo protegido que o useAuthGuard sozinho
 * não evita (ele só redireciona depois de montar, em um useEffect).
 *
 * Isto não é a autorização de verdade: o access_token vive em localStorage
 * (invisível aqui, que roda no edge) e o refresh token é um cookie httpOnly
 * escopado ao domínio/path da API (também invisível aqui). O middleware só
 * enxerga o cookie SESSION_HINT_COOKIE, que não tem privilégio nenhum — quem
 * continua barrando de verdade é o useAuthGuard (por role) e o backend (via
 * require_roles) em cada chamada de API.
 */
const PROTECTED_PREFIXES = Object.keys(ROUTE_ROLES);

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  if (!isProtected) return NextResponse.next();

  if (request.cookies.has(SESSION_HINT_COOKIE)) return NextResponse.next();

  // Nenhuma rota do portal do cliente está em ROUTE_ROLES hoje (é só um
  // stub) — por isso sempre /login/admin aqui. Se o portal for construído de
  // verdade, dá pra reintroduzir a bifurcação por /cliente com teste próprio.
  const url = request.nextUrl.clone();
  url.pathname = '/login/admin';
  url.search = '';
  return NextResponse.redirect(url);
}

export const config = {
  // Roda em tudo exceto assets estáticos — o filtro por rota protegida de
  // verdade acontece dentro da função, usando ROUTE_ROLES como única fonte.
  matcher: ['/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|map|json|woff2?)$).*)'],
};
