import { NextRequest } from 'next/server';
import { describe, expect, it } from 'vitest';

import { middleware } from './middleware';
import { SESSION_HINT_COOKIE } from './lib/auth';

function makeRequest(path: string, withSession = false) {
  const request = new NextRequest(new URL(path, 'http://localhost:3000'));
  if (withSession) request.cookies.set(SESSION_HINT_COOKIE, '1');
  return request;
}

function redirectPath(response: ReturnType<typeof middleware>) {
  const location = response.headers.get('location');
  return location ? new URL(location).pathname : null;
}

describe('middleware', () => {
  it('deixa passar uma rota pública mesmo sem cookie de sessão', () => {
    const response = middleware(makeRequest('/login/admin'));
    expect(redirectPath(response)).toBeNull();
  });

  it('deixa passar a landing page sem cookie de sessão', () => {
    const response = middleware(makeRequest('/'));
    expect(redirectPath(response)).toBeNull();
  });

  it('redireciona uma rota protegida para /login/admin quando não há sinal de sessão', () => {
    const response = middleware(makeRequest('/clientes'));
    expect(redirectPath(response)).toBe('/login/admin');
  });

  it('deixa passar uma rota protegida quando o cookie de sessão existe', () => {
    const response = middleware(makeRequest('/clientes', true));
    expect(redirectPath(response)).toBeNull();
  });

  it('também protege sub-rotas (prefixo), não só o path exato', () => {
    const response = middleware(makeRequest('/clientes/relatorio-x'));
    expect(redirectPath(response)).toBe('/login/admin');
  });

  it('não mexe em /cliente/dashboard — não está em ROUTE_ROLES (é só um stub que redireciona sozinho)', () => {
    const response = middleware(makeRequest('/cliente/dashboard'));
    expect(redirectPath(response)).toBeNull();
  });
});
