import { describe, expect, it, vi } from 'vitest';

import {
  excluirComConfirmacoes,
  mensagemConfirmacaoExclusao,
  motivoBloqueioExclusao,
  type CobrancaParaExcluir,
} from './exclusao-cobranca';

const cancelada: CobrancaParaExcluir = {
  id: 7, status: 'cancelada', billing_type: 'avulsa', contract_id: null, titulo_bancario: { estado: 'sem_titulo' },
};

function conflito(code: string, message = `msg ${code}`) {
  return Object.assign(new Error(message), { status: 409, detail: { code, message } });
}

describe('motivoBloqueioExclusao', () => {
  it('libera cancelada sem boleto no banco', () => {
    expect(motivoBloqueioExclusao(cancelada)).toBeNull();
    expect(motivoBloqueioExclusao({ ...cancelada, titulo_bancario: null })).toBeNull();
  });

  it('bloqueia o que não está cancelado', () => {
    expect(motivoBloqueioExclusao({ ...cancelada, status: 'pendente' })).toMatch(/cancelada/);
    expect(motivoBloqueioExclusao({ ...cancelada, status: 'paga' })).toMatch(/cancelada/);
  });

  it.each(['registrado', 'baixado', 'em_registro', 'desfecho_desconhecido', 'remessa_cnab'] as const)(
    'bloqueia cancelada com título %s no banco',
    (estado) => {
      expect(motivoBloqueioExclusao({ ...cancelada, titulo_bancario: { estado } })).toMatch(/banco/);
    },
  );
});

describe('mensagemConfirmacaoExclusao', () => {
  it('avisa que a mensalidade libera o mês para o fechamento', () => {
    const msg = mensagemConfirmacaoExclusao({
      ...cancelada, billing_type: 'recorrente', contract_id: 3, period_label: '09/2026',
    });
    expect(msg).toContain('competência 09/2026');
    expect(msg).toContain('fechamento pode gerar a cobrança de novo');
  });

  it('sem aviso de competência para avulsa ou competência já liberada', () => {
    expect(mensagemConfirmacaoExclusao(cancelada)).not.toContain('competência');
    expect(mensagemConfirmacaoExclusao({
      ...cancelada, billing_type: 'recorrente', contract_id: 3, competencia_liberada: true,
    })).not.toContain('competência');
  });
});

describe('excluirComConfirmacoes', () => {
  it('exclui direto quando o backend aceita', async () => {
    const chamar = vi.fn().mockResolvedValue({ message: 'ok', reabertas: [] });
    const confirmar = vi.fn();
    expect(await excluirComConfirmacoes(chamar, confirmar)).toEqual({ excluida: true, reabertas: [] });
    expect(chamar).toHaveBeenCalledWith(false);
    expect(confirmar).not.toHaveBeenCalled();
  });

  it('boleto único: reabre as originais só se o operador confirmar', async () => {
    const chamar = vi.fn(async (reverter: boolean) => {
      if (!reverter) throw conflito('titulo_substituto', 'Esta cobrança substitui #1, #2.');
      return { reabertas: [1, 2] };
    });
    const confirmar = vi.fn().mockReturnValue(true);
    expect(await excluirComConfirmacoes(chamar, confirmar)).toEqual({ excluida: true, reabertas: [1, 2] });
    expect(confirmar).toHaveBeenCalledWith(expect.stringContaining('substitui #1, #2.'));
    expect(chamar.mock.calls.map((c) => c[0])).toEqual([false, true]);
  });

  it('recusar a reversão não exclui', async () => {
    const chamar = vi.fn().mockRejectedValue(conflito('titulo_substituto'));
    expect(await excluirComConfirmacoes(chamar, () => false)).toEqual({ excluida: false, reabertas: [] });
    expect(chamar).toHaveBeenCalledTimes(1);
  });

  it('outras recusas do backend sobem para a tela', async () => {
    const chamar = vi.fn().mockRejectedValue(conflito('titulo_substituido', 'Substituída pela #9.'));
    await expect(excluirComConfirmacoes(chamar, () => true)).rejects.toThrow('Substituída pela #9.');
  });
});
