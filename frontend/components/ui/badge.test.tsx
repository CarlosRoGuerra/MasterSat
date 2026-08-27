import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge, statusLabel, statusVariant } from './badge';

describe('statusVariant', () => {
  it('mapeia status conhecidos para a variante correta', () => {
    expect(statusVariant('ativo')).toBe('success');
    expect(statusVariant('inadimplente')).toBe('danger');
    expect(statusVariant('em_estoque')).toBe('info');
    expect(statusVariant('pendente')).toBe('warning');
  });

  it('cai em "default" para status desconhecido', () => {
    expect(statusVariant('status_que_nao_existe')).toBe('default');
  });
});

describe('statusLabel', () => {
  it('traduz status conhecidos para PT-BR', () => {
    expect(statusLabel('em_analise')).toBe('Em análise');
    expect(statusLabel('reenvio_solicitado')).toBe('Reenvio solicitado');
  });

  it('devolve o próprio valor quando não há tradução mapeada', () => {
    expect(statusLabel('valor_nao_mapeado')).toBe('valor_nao_mapeado');
  });
});

describe('Badge', () => {
  it('renderiza o conteúdo e aplica a classe da variante', () => {
    render(<Badge variant="danger">Vencida</Badge>);
    const badge = screen.getByText('Vencida');
    expect(badge.className).toContain('text-rose-700');
  });

  it('usa a variante "default" quando nenhuma é informada', () => {
    render(<Badge>Neutro</Badge>);
    expect(screen.getByText('Neutro').className).toContain('text-slate-600');
  });
});
