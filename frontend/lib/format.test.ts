import { describe, expect, it } from 'vitest';

import {
  formatCpfCnpj,
  formatPhone,
  formatZipCode,
  intervalLabel,
  onlyDigits,
  pricePeriodSuffix,
  validatePassword,
} from './format';

describe('onlyDigits', () => {
  it('remove tudo que não for dígito', () => {
    expect(onlyDigits('(47) 99999-9999')).toBe('47999999999');
  });
});

describe('formatCpfCnpj', () => {
  it('formata CPF (11 dígitos)', () => {
    expect(formatCpfCnpj('12345678901')).toBe('123.456.789-01');
  });

  it('formata CNPJ (14 dígitos)', () => {
    expect(formatCpfCnpj('12345678000199')).toBe('12.345.678/0001-99');
  });
});

describe('formatPhone', () => {
  it('formata telefone fixo (10 dígitos)', () => {
    expect(formatPhone('4733334444')).toBe('(47) 3333-4444');
  });

  it('formata celular (11 dígitos)', () => {
    expect(formatPhone('47999998888')).toBe('(47) 99999-8888');
  });
});

describe('formatZipCode', () => {
  it('formata CEP', () => {
    expect(formatZipCode('89201000')).toBe('89201-000');
  });
});

describe('validatePassword', () => {
  it('reporta cada critério individualmente', () => {
    expect(validatePassword('abc')).toEqual({
      minLength: false,
      upper: false,
      lower: true,
      number: false,
      special: false,
    });
    expect(validatePassword('Abcdef1!')).toEqual({
      minLength: true,
      upper: true,
      lower: true,
      number: true,
      special: true,
    });
  });
});

describe('intervalLabel', () => {
  it('mapeia meses conhecidos', () => {
    expect(intervalLabel(1)).toBe('Mensal');
    expect(intervalLabel(3)).toBe('Trimestral');
    expect(intervalLabel(6)).toBe('Semestral');
    expect(intervalLabel(12)).toBe('Anual');
  });

  it('usa fallback para meses não mapeados', () => {
    expect(intervalLabel(4)).toBe('A cada 4 meses');
  });

  it('usa 1 mês quando não informado', () => {
    expect(intervalLabel(undefined)).toBe('Mensal');
  });
});

describe('pricePeriodSuffix', () => {
  it('diferencia mensal, anual e demais periodicidades', () => {
    expect(pricePeriodSuffix(1)).toBe('/mês');
    expect(pricePeriodSuffix(12)).toBe('/ano');
    expect(pricePeriodSuffix(3)).toBe(' a cada 3 meses');
  });
});
