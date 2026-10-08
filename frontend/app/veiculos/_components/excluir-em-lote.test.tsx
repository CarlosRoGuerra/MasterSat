import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ExcluirVeiculosEmLote, type VehicleDeleteResult } from './excluir-em-lote';

function result(simulacao: boolean): VehicleDeleteResult {
  return { simulacao, total_enviados: 2, aplicados: 1, ignorados: 1, itens: [
    { vehicle_id: 1, plate: 'TRATOR', situacao: 'aplicado', motivo: null },
    { vehicle_id: 2, plate: 'MHW0459', situacao: 'ignorado', motivo: 'Existe contrato ativo.' },
  ] };
}

describe('Exclusão em lote dos veículos', () => {
  it('confere a seleção, informa os bloqueados e aplica somente após confirmar', async () => {
    const execute = vi.fn(async (body: { simular: boolean }) => result(body.simular));
    const done = vi.fn();
    const user = userEvent.setup();
    render(<ExcluirVeiculosEmLote ids={[1, 2]} onLimpar={vi.fn()} onExecutar={execute} onConcluido={done} />);
    await user.click(screen.getByRole('button', { name: 'Excluir selecionados' }));
    expect(execute).toHaveBeenCalledExactlyOnceWith({ ids: [1, 2], simular: true });
    expect(await screen.findByText(/Existe contrato ativo/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Excluir 1 veículo(s)' }));
    expect(execute).toHaveBeenLastCalledWith({ ids: [1, 2], simular: false });
    await waitFor(() => expect(done).toHaveBeenCalledWith(result(false)));
  });

  it('cancelar não exclui cadastros', async () => {
    const execute = vi.fn(async () => result(true));
    const user = userEvent.setup();
    render(<ExcluirVeiculosEmLote ids={[1, 2]} onLimpar={vi.fn()} onExecutar={execute} onConcluido={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Excluir selecionados' }));
    await screen.findByText(/Existe contrato ativo/);
    await user.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('falha de conferência impede confirmar', async () => {
    const execute = vi.fn().mockRejectedValue(new Error('Sessão expirada'));
    const user = userEvent.setup();
    render(<ExcluirVeiculosEmLote ids={[1]} onLimpar={vi.fn()} onExecutar={execute} onConcluido={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Excluir selecionados' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sessão expirada');
    expect(screen.getByRole('button', { name: 'Excluir 0 veículo(s)' })).toBeDisabled();
  });
});
