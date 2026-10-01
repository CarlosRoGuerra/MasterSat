import { describe, expect, it } from 'vitest';

import { camposDiferenca, diferencaRecebimento, paraCentavos, pendenciaDoFormulario } from './recebimento';

const form = (paid_amount: string, extra: Partial<Parameters<typeof pendenciaDoFormulario>[1]> = {}) => ({
  paid_amount, tratamento: '' as const, justificativa: '', saldo_vencimento: '', ...extra,
});

describe('diferencaRecebimento', () => {
  it('compara em centavos (0,1 + 0,2 é 0,30)', () => {
    expect(diferencaRecebimento(0.3, 0.1 + 0.2)?.tipo).toBe('igual');
    expect(paraCentavos('99.9')).toBe(9990); // como a tela pré-preenche
    expect(paraCentavos('99,90')).toBe(9990);
    expect(paraCentavos('abc')).toBeNull();
  });

  it('menor oferece desconto/parcial; maior oferece encargos/crédito', () => {
    expect(diferencaRecebimento(100, '1')).toEqual({ centavos: -9900, tipo: 'menor', tratamentos: ['desconto', 'parcial'] });
    expect(diferencaRecebimento(100, '103,00')).toEqual({ centavos: 300, tipo: 'maior', tratamentos: ['encargos', 'credito'] });
  });
});

describe('pendenciaDoFormulario', () => {
  it('valor igual não pede nada', () => {
    expect(pendenciaDoFormulario(100, form('100'))).toBeNull();
    expect(camposDiferenca(100, form('100'))).toEqual({});
  });

  it('zero e negativo são recusados', () => {
    expect(pendenciaDoFormulario(100, form('0'))).toMatch(/maior que zero/);
    expect(pendenciaDoFormulario(100, form('-5'))).toMatch(/maior que zero/);
  });

  it('diferença exige tratamento compatível e justificativa', () => {
    expect(pendenciaDoFormulario(100, form('1'))).toMatch(/escolha/);
    expect(pendenciaDoFormulario(100, form('1', { tratamento: 'encargos' }))).toMatch(/escolha/);
    expect(pendenciaDoFormulario(100, form('1', { tratamento: 'desconto' }))).toMatch(/Justifique/);
    expect(pendenciaDoFormulario(100, form('1', { tratamento: 'parcial', justificativa: 'pagou 1' }))).toMatch(/vencimento/);
    expect(pendenciaDoFormulario(100, form('103', { tratamento: 'encargos' }))).toBeNull();
  });

  it('monta os campos do backend', () => {
    expect(camposDiferenca(100, form('1', { tratamento: 'parcial', justificativa: ' pagou 1 ', saldo_vencimento: '2099-12-10' })))
      .toEqual({ tratamento_diferenca: 'parcial', justificativa_diferenca: 'pagou 1', saldo_vencimento: '2099-12-10' });
  });
});
