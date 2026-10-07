import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AcoesEmLote, type AcaoLoteCorpo, type AcaoLoteResultado } from './acoes-em-lote';

function resultado(simulacao: boolean): AcaoLoteResultado {
  return {
    simulacao, total_enviados: 3, aplicados: 2, ignorados: 1,
    itens: [
      { tracker_id: 1, imei: '869671075762888', situacao: 'aplicado' },
      { tracker_id: 2, imei: '865413058613315', situacao: 'aplicado' },
      { tracker_id: 3, imei: '54100185', situacao: 'ignorado', motivo: 'Só é possível excluir rastreador extraviado ou em manutenção' },
    ],
  };
}

const executar = () => vi.fn((_acao: string, corpo: AcaoLoteCorpo) => Promise.resolve(resultado(corpo.simular)));

describe('Ações em lote dos rastreadores', () => {
  it('não aparece sem seleção', () => {
    render(<AcoesEmLote ids={[]} onLimpar={vi.fn()} onExecutar={executar()} onConcluido={vi.fn()} />);
    expect(screen.queryByText(/selecionado/)).not.toBeInTheDocument();
  });

  it('exclusão confere antes, mostra quem fica de fora e só então aplica', async () => {
    const onExecutar = executar();
    const onConcluido = vi.fn();
    const user = userEvent.setup();
    render(<AcoesEmLote ids={[1, 2, 3]} onLimpar={vi.fn()} onExecutar={onExecutar} onConcluido={onConcluido} />);
    expect(screen.getByText('3 selecionado(s)')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Excluir' }));
    expect(onExecutar).toHaveBeenCalledWith('excluir', { ids: [1, 2, 3], simular: true });
    expect(await screen.findByText('2 serão excluído(s)')).toBeInTheDocument();
    expect(screen.getByText('54100185')).toBeInTheDocument();
    expect(screen.getByText(/extraviado ou em manutenção/, { selector: 'span' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Excluir 2 rastreador(es)' }));
    expect(onExecutar).toHaveBeenLastCalledWith('excluir', { ids: [1, 2, 3], simular: false });
    await waitFor(() => expect(onConcluido).toHaveBeenCalledWith('2 rastreador(es) excluído(s). 1 ficaram de fora.'));
  });

  it('alterar status exige escolher o status e não oferece "instalado"', async () => {
    const onExecutar = executar();
    const onConcluido = vi.fn();
    const user = userEvent.setup();
    render(<AcoesEmLote ids={[1, 2, 3]} onLimpar={vi.fn()} onExecutar={onExecutar} onConcluido={onConcluido} />);

    await user.click(screen.getByRole('button', { name: 'Alterar status' }));
    expect(onExecutar).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Alterar 0 rastreador(es)' })).toBeDisabled();
    const opcoes = Array.from((screen.getByLabelText('Novo status') as HTMLSelectElement).options).map((o) => o.value);
    expect(opcoes).toEqual(['', 'em_estoque', 'em_manutencao', 'extraviado', 'descartado']);

    await user.selectOptions(screen.getByLabelText('Novo status'), 'extraviado');
    expect(onExecutar).toHaveBeenCalledWith('status', { ids: [1, 2, 3], simular: true, status: 'extraviado' });
    await user.type(screen.getByLabelText(/Observação/), 'inventário');
    await user.click(await screen.findByRole('button', { name: 'Alterar 2 rastreador(es)' }));
    expect(onExecutar).toHaveBeenLastCalledWith('status', {
      ids: [1, 2, 3], simular: false, status: 'extraviado', notes: 'inventário',
    });
    await waitFor(() => expect(onConcluido).toHaveBeenCalledWith(
      'Status de 2 rastreador(es) alterado para "Extraviado". 1 ficaram de fora.',
    ));
  });

  it('mostra o erro do servidor e mantém a janela aberta', async () => {
    const onExecutar = vi.fn((_acao: string, corpo: AcaoLoteCorpo) => (
      corpo.simular ? Promise.resolve(resultado(true)) : Promise.reject(new Error('Sessão expirada'))
    ));
    const user = userEvent.setup();
    render(<AcoesEmLote ids={[1, 2, 3]} onLimpar={vi.fn()} onExecutar={onExecutar} onConcluido={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Excluir' }));
    await user.click(await screen.findByRole('button', { name: 'Excluir 2 rastreador(es)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sessão expirada');
    expect(screen.getByRole('button', { name: 'Excluir 2 rastreador(es)' })).toBeInTheDocument();
  });
});
