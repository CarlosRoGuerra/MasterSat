import { describe, expect, it, vi } from 'vitest';

import { parcelarLiberandoCanceladas } from './parcelar-carne';

function conflito(code: string, message = `msg ${code}`) {
  return Object.assign(new Error(message), { status: 409, detail: { code, message } });
}

describe('parcelarLiberandoCanceladas', () => {
  it('gera direto quando nenhum mês está preso', async () => {
    const parcelar = vi.fn().mockResolvedValue([{ id: 1 }]);
    const confirmar = vi.fn();
    expect(await parcelarLiberandoCanceladas(parcelar, confirmar)).toEqual([{ id: 1 }]);
    expect(parcelar.mock.calls).toEqual([[false]]);
    expect(confirmar).not.toHaveBeenCalled();
  });

  it('libera meses de canceladas só depois de o operador confirmar o aviso do banco', async () => {
    const parcelar = vi.fn(async (liberar: boolean) => {
      if (!liberar) throw conflito('competencias_canceladas', 'Providencie a baixa no site do Sicredi.');
      return [{ id: 2 }];
    });
    const confirmar = vi.fn().mockReturnValue(true);
    expect(await parcelarLiberandoCanceladas(parcelar, confirmar)).toEqual([{ id: 2 }]);
    expect(confirmar).toHaveBeenCalledWith(expect.stringContaining('site do Sicredi'));
    expect(parcelar.mock.calls).toEqual([[false], [true]]);
  });

  it('não gera nada quando o operador recusa', async () => {
    const parcelar = vi.fn().mockRejectedValue(conflito('competencias_canceladas'));
    expect(await parcelarLiberandoCanceladas(parcelar, () => false)).toBeNull();
    expect(parcelar).toHaveBeenCalledTimes(1);
  });

  it('repassa outros conflitos sem perguntar', async () => {
    const erro = Object.assign(new Error('Este contrato já tem cobrança de mensalidade lançada'), { status: 409, detail: undefined });
    const confirmar = vi.fn();
    await expect(parcelarLiberandoCanceladas(vi.fn().mockRejectedValue(erro), confirmar)).rejects.toBe(erro);
    expect(confirmar).not.toHaveBeenCalled();
  });
});
