import { describe, expect, it, vi } from 'vitest';

import { cancelarComConfirmacoes, type FlagsCancelamento } from './cancelamento-cobranca';

function conflito(code: string, message = `msg ${code}`) {
  return Object.assign(new Error(message), { status: 409, detail: { code, message } });
}

describe('cancelarComConfirmacoes', () => {
  it('cancela direto quando o backend não pede confirmação', async () => {
    const chamar = vi.fn().mockResolvedValue({});
    const confirmar = vi.fn();
    const r = await cancelarComConfirmacoes(chamar, confirmar);
    expect(r.cancelada).toBe(true);
    expect(chamar).toHaveBeenCalledTimes(1);
    expect(confirmar).not.toHaveBeenCalled();
  });

  it('reverte a substituição só depois de o operador confirmar', async () => {
    const chamadas: FlagsCancelamento[] = [];
    const chamar = vi.fn(async (flags: FlagsCancelamento) => {
      chamadas.push(flags);
      if (!flags.reverter_substituicao) throw conflito('titulo_substituto', 'Substitui #1, #2.');
    });
    const confirmar = vi.fn().mockReturnValue(true);
    const r = await cancelarComConfirmacoes(chamar, confirmar);
    expect(r).toEqual({ cancelada: true, flags: { confirmar_boleto_ailos: false, reverter_substituicao: true } });
    expect(confirmar).toHaveBeenCalledWith(expect.stringContaining('Substitui #1, #2.'));
    expect(chamadas.map((f) => f.reverter_substituicao)).toEqual([false, true]);
  });

  it('pede as duas confirmações quando o substituto também tem boleto registrado', async () => {
    const chamar = vi.fn(async (flags: FlagsCancelamento) => {
      if (!flags.reverter_substituicao) throw conflito('titulo_substituto');
      if (!flags.confirmar_boleto_ailos) throw conflito('boleto_ailos_registrado');
    });
    const r = await cancelarComConfirmacoes(chamar, () => true);
    expect(r.flags).toEqual({ confirmar_boleto_ailos: true, reverter_substituicao: true });
    expect(chamar).toHaveBeenCalledTimes(3);
  });

  it('não cancela quando o operador recusa', async () => {
    const chamar = vi.fn().mockRejectedValue(conflito('titulo_substituto'));
    const r = await cancelarComConfirmacoes(chamar, () => false);
    expect(r.cancelada).toBe(false);
    expect(chamar).toHaveBeenCalledTimes(1);
  });

  it('não entra em laço se o backend repetir o mesmo pedido', async () => {
    const chamar = vi.fn().mockRejectedValue(conflito('titulo_substituto'));
    await expect(cancelarComConfirmacoes(chamar, () => true)).rejects.toThrow('msg titulo_substituto');
    expect(chamar).toHaveBeenCalledTimes(2);
  });

  it('repassa outros erros sem perguntar nada', async () => {
    const erro = Object.assign(new Error('Cobrança já está cancelada.'), { status: 400 });
    const confirmar = vi.fn();
    await expect(cancelarComConfirmacoes(vi.fn().mockRejectedValue(erro), confirmar)).rejects.toBe(erro);
    expect(confirmar).not.toHaveBeenCalled();
  });
});
