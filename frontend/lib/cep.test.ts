import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchAddressByCep } from './cep';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchAddressByCep', () => {
  it('não consulta a API quando o CEP não tem 8 dígitos', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAddressByCep('123');

    expect(result).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ignora a formatação (traço) e consulta só os dígitos', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        zip_code: '89201000',
        address_line: 'Rua X',
        neighborhood: 'Centro',
        city: 'Joinville',
        state: 'SC',
        address_complement: '',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAddressByCep('89201-000');

    expect(result?.city).toBe('Joinville');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('/utils/cep/89201000');
  });

  it('propaga a mensagem de erro do backend quando a consulta falha', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'CEP não encontrado.' }),
    }));

    await expect(fetchAddressByCep('89201000')).rejects.toThrow('CEP não encontrado.');
  });

  it('usa mensagem genérica quando a resposta de erro não tem JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: async () => { throw new Error('not json'); },
    }));

    await expect(fetchAddressByCep('89201000')).rejects.toThrow(
      'Não foi possível consultar o CEP no momento.',
    );
  });
});
