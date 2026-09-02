import { describe, expect, it } from 'vitest';

import {
  contractSituacao,
  envioMeta,
  formatCurrency,
  normalizeEmail,
  parseError,
  parseExtraEmails,
} from './helpers';
import type { ContractSheetItem } from './types';

describe('formatCurrency', () => {
  it('formata em real com duas casas', () => {
    expect(formatCurrency(1234.5)).toBe('R$ 1.234,50');
  });

  it('trata null/undefined como zero em vez de quebrar', () => {
    expect(formatCurrency(null as unknown as number)).toBe('R$ 0,00');
    expect(formatCurrency(undefined as unknown as number)).toBe('R$ 0,00');
  });
});

describe('normalizeEmail', () => {
  it('remove espaços nas pontas e força minúsculas', () => {
    expect(normalizeEmail('  Joao@Empresa.COM  ')).toBe('joao@empresa.com');
  });
});

describe('parseExtraEmails', () => {
  it('aceita vírgula, ponto-e-vírgula e quebra de linha como separador', () => {
    expect(parseExtraEmails('a@a.com, b@b.com;c@c.com\nd@d.com')).toEqual([
      'a@a.com', 'b@b.com', 'c@c.com', 'd@d.com',
    ]);
  });

  it('normaliza (trim + minúsculas) e descarta entradas vazias', () => {
    expect(parseExtraEmails(' A@A.COM ,, ;  \n B@B.COM')).toEqual(['a@a.com', 'b@b.com']);
  });

  it('string vazia vira lista vazia', () => {
    expect(parseExtraEmails('')).toEqual([]);
  });
});

describe('parseError', () => {
  it('usa a mensagem quando é um Error', () => {
    expect(parseError(new Error('deu ruim'))).toBe('deu ruim');
  });

  it('usa mensagem genérica para qualquer outra coisa lançada', () => {
    expect(parseError('string crua')).toBe('Ocorreu um erro inesperado.');
    expect(parseError(undefined)).toBe('Ocorreu um erro inesperado.');
  });
});

describe('contractSituacao', () => {
  const base: ContractSheetItem = {
    id: 1,
    start_date: '2025-01-01',
    status: 'ativo',
    signed: true,
    end_date: null,
  };

  it('cancelado tem prioridade mesmo se assinado e dentro do prazo', () => {
    expect(contractSituacao({ ...base, status: 'cancelado', end_date: '2099-01-01' }))
      .toEqual({ label: 'Cancelado', variant: 'warning' });
  });

  it('encerrado também cai em "Cancelado"', () => {
    expect(contractSituacao({ ...base, status: 'encerrado' }))
      .toEqual({ label: 'Cancelado', variant: 'warning' });
  });

  it('não assinado aguarda assinatura, mesmo sem vencimento', () => {
    expect(contractSituacao({ ...base, signed: false }))
      .toEqual({ label: 'Aguardando assinatura', variant: 'warning' });
  });

  it('não assinado tem prioridade sobre um end_date já vencido', () => {
    expect(contractSituacao({ ...base, signed: false, end_date: '2000-01-01' }))
      .toEqual({ label: 'Aguardando assinatura', variant: 'warning' });
  });

  it('assinado e sem end_date fica em vigor', () => {
    expect(contractSituacao({ ...base, end_date: null }))
      .toEqual({ label: 'Em vigor', variant: 'success' });
  });

  it('assinado com end_date no futuro fica em vigor', () => {
    expect(contractSituacao({ ...base, end_date: '2099-12-31' }))
      .toEqual({ label: 'Em vigor', variant: 'success' });
  });

  it('assinado com end_date no passado fica vencido', () => {
    expect(contractSituacao({ ...base, end_date: '2000-01-01' }))
      .toEqual({ label: 'Vencido', variant: 'danger' });
  });
});

describe('envioMeta', () => {
  it('sem uploaded_by nem created_at retorna null', () => {
    expect(envioMeta({})).toBeNull();
  });

  it('só com uploaded_by', () => {
    expect(envioMeta({ uploaded_by: 'Ana' })).toBe('enviado por Ana');
  });

  it('só com created_at', () => {
    expect(envioMeta({ created_at: '2026-08-12T10:00:00Z' })).toBe('enviado em 12/08/2026');
  });

  it('com os dois combina "enviado por X em DD/MM/AAAA"', () => {
    expect(envioMeta({ uploaded_by: 'Ana', created_at: '2026-08-12T10:00:00Z' }))
      .toBe('enviado por Ana em 12/08/2026');
  });
});
