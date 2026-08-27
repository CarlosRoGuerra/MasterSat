import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiFetch } from './api';

const originalLocation = window.location;

function mockLocation(pathname: string) {
  // jsdom lança "Not implemented: navigation" numa atribuição real a
  // window.location.href — substituímos por um objeto simples só pra
  // conseguir observar o valor sem navegar de verdade.
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...originalLocation, href: '', pathname },
  });
}

beforeEach(() => {
  localStorage.clear();
  mockLocation('/dashboard');
});

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

describe('apiFetch', () => {
  it('usa o token mais recente do localStorage, mesmo se um token antigo for passado', async () => {
    localStorage.setItem('access_token', 'stored-token');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ hello: 'world' }) });
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiFetch<{ hello: string }>('/ping', {}, 'stale-token');

    expect(result).toEqual({ hello: 'world' });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer stored-token');
  });

  it('renova o access token em um 401 e repete a requisição original', async () => {
    localStorage.setItem('access_token', 'expired-token');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) }) // chamada original
      .mockResolvedValueOnce({ ok: true, json: async () => ({ access_token: 'fresh-token' }) }) // /auth/refresh
      .mockResolvedValueOnce({ ok: true, json: async () => ({ data: 'ok' }) }); // retry com token novo
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiFetch<{ data: string }>('/clientes', {}, 'expired-token');

    expect(result).toEqual({ data: 'ok' });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(localStorage.getItem('access_token')).toBe('fresh-token');
  });

  it('desloga e redireciona quando o refresh também falha', async () => {
    localStorage.setItem('access_token', 'expired-token');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) }) // chamada original
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) }); // /auth/refresh falha
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/clientes', {}, 'expired-token')).rejects.toThrow('Sessão expirada.');

    expect(localStorage.getItem('access_token')).toBeNull();
    expect(window.location.href).toBe('/login/admin');
  });

  it('propaga a mensagem de erro do backend sem redirecionar quando não há sessão (ex.: login)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Credenciais inválidas.' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/auth/login', { method: 'POST' })).rejects.toThrow('Credenciais inválidas.');
    expect(window.location.href).toBe('');
  });
});
