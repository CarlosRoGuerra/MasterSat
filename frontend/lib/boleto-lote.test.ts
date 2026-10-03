import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch }));

import { processarBoletosSelecionados, separarBoletosPorRegistro } from '@/lib/boleto-lote';

describe('ações em lote dos boletos selecionados', () => {
  beforeEach(() => apiFetch.mockReset());

  it('ignora boletos registrados e preserva a ordem dos ausentes sem duplicar IDs', () => {
    expect(separarBoletosPorRegistro([12, 8, 12, 5], [
      { id: 12, boleto_ailos: true }, { id: 8, boleto_ailos: false }, { id: 5, boleto_ailos: true },
    ])).toEqual({ existentes: [12, 5], ausentes: [8] });
  });

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

  it('permite escolher boleto e NFS-e juntos ou enviar somente a NFS-e', async () => {
    apiFetch.mockResolvedValue({});

    await processarBoletosSelecionados([7], 'email_com_nfse', 'token');
    await processarBoletosSelecionados([8], 'nfse_email', 'token');

    expect(apiFetch).toHaveBeenNthCalledWith(1, '/boletos/7/enviar-email?incluir_nfse=true', { method: 'POST' }, 'token');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/nfse/8/enviar-email', { method: 'POST' }, 'token');
  });
});
