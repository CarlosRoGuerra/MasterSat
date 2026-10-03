import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch }));

import { processarBoletosSelecionados } from '@/lib/boleto-lote';

describe('ações em lote dos boletos selecionados', () => {
  beforeEach(() => apiFetch.mockReset());

  it('emite apenas os IDs selecionados, uma vez cada, e não envia e-mail', async () => {
    apiFetch.mockResolvedValue({ linha_digitavel: '123', codigo_barras: '456' });
    const progresso = vi.fn();

    const resultado = await processarBoletosSelecionados([12, 8, 12], 'emitir', 'token', progresso);

    expect(resultado).toEqual({ processados: [12, 8], falhas: [] });
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/ailos/boletos', {
      method: 'POST', body: JSON.stringify({ billing_id: 12 }),
    }, 'token');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/ailos/boletos', {
      method: 'POST', body: JSON.stringify({ billing_id: 8 }),
    }, 'token');
    expect(progresso).toHaveBeenLastCalledWith(2, 2);
  });

  it('envia os demais e-mails mesmo quando um boleto falha', async () => {
    apiFetch.mockRejectedValueOnce(new Error('Boleto sem registro na Ailos')).mockResolvedValueOnce({});

    const resultado = await processarBoletosSelecionados([4, 5], 'email', 'token');

    expect(resultado).toEqual({
      processados: [5], falhas: [{ id: 4, mensagem: 'Boleto sem registro na Ailos' }],
    });
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/boletos/4/enviar-email', { method: 'POST' }, 'token');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/boletos/5/enviar-email', { method: 'POST' }, 'token');
  });

  it('não conta como emitido um boleto sem os dados bancários oficiais', async () => {
    apiFetch.mockResolvedValue({ linha_digitavel: '123', codigo_barras: null });

    const resultado = await processarBoletosSelecionados([9], 'emitir', 'token');

    expect(resultado.processados).toEqual([]);
    expect(resultado.falhas[0]).toMatchObject({ id: 9 });
  });
});
