import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AVISO_NAO_REGISTRADO, enviarBoletoEmail, enviarBoletoWhats, renderTemplate } from './boleto-mensagem';

describe('renderTemplate', () => {
  it('substitui placeholders {VAR} pelos valores informados', () => {
    expect(renderTemplate('Olá {NOME}, seu boleto de {VALOR} vence em {VENCIMENTO}.', {
      NOME: 'JOÃO',
      VALOR: '150,00',
      VENCIMENTO: '10/09/2026',
    })).toBe('Olá JOÃO, seu boleto de 150,00 vence em 10/09/2026.');
  });

  it('substitui placeholder sem valor correspondente por string vazia', () => {
    expect(renderTemplate('Ref: {REFERENTE}', {})).toBe('Ref: ');
  });
});

describe('enviarBoletoWhats / enviarBoletoEmail', () => {
  const billing = { id: 1, amount: 150, due_date: '2026-09-10', period_label: 'Setembro/2026' };

  beforeEach(() => {
    vi.stubGlobal('open', vi.fn());
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/boletos/')) {
        return {
          ok: true,
          json: async () => ({ linha_digitavel: '123', public_pdf_url: 'https://x/boleto.pdf', boleto_registrado: true }),
        };
      }
      return {
        ok: true,
        json: async () => ({ msg_boleto: 'Olá {NOME}, boleto {VALOR}', msg_boleto_assunto: 'Boleto {NOME}' }),
      };
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('recusa enviar por WhatsApp sem telefone cadastrado', async () => {
    await expect(enviarBoletoWhats(billing, { name: 'Maria' }, 'token')).rejects.toThrow(
      'Cliente sem telefone cadastrado.',
    );
  });

  it('recusa enviar por e-mail sem e-mail cadastrado', async () => {
    await expect(enviarBoletoEmail(billing, { name: 'Maria' }, 'token')).rejects.toThrow(
      'Cliente sem e-mail cadastrado.',
    );
  });

  it('abre o wa.me com a mensagem do template preenchida', async () => {
    await enviarBoletoWhats(billing, { name: 'Maria', phone: '(47) 99999-8888' }, 'token');

    expect(window.open).toHaveBeenCalledTimes(1);
    const [url] = (window.open as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('https://wa.me/5547999998888?text=');
    expect(decodeURIComponent(url as string)).toContain('Olá MARIA, boleto 150,00');
  });

  it('recusa envio quando o boleto ainda não está registrado na Ailos', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ linha_digitavel: '', boleto_registrado: false }),
    })));

    await expect(
      enviarBoletoWhats(billing, { name: 'Maria', phone: '47999998888' }, 'token'),
    ).rejects.toThrow(AVISO_NAO_REGISTRADO);
  });
});
