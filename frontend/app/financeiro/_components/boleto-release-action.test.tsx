import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { BoletoReleaseAction } from './boleto-release-action';
import type { TituloBancario } from '@/lib/titulo-bancario';

const props = {
  billingId: 38568,
  billingStatus: 'pendente',
  titulo: { estado: 'desfecho_desconhecido' as const },
  isAdmin: true,
};

describe('Liberação administrativa de boleto', () => {
  it('não oferece a liberação para outro perfil ou para título fora do estado permitido', () => {
    const onRelease = vi.fn();
    const { rerender } = render(<BoletoReleaseAction {...props} isAdmin={false} onRelease={onRelease} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    for (const estado of ['registrado', 'baixado', 'em_registro', 'sem_titulo', 'remessa_cnab'] as TituloBancario['estado'][]) {
      rerender(<BoletoReleaseAction {...props} titulo={{ estado }} onRelease={onRelease} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    }
    for (const billingStatus of ['paga', 'cancelada']) {
      rerender(<BoletoReleaseAction {...props} billingStatus={billingStatus} onRelease={onRelease} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    }
    expect(onRelease).not.toHaveBeenCalled();
  });

  it('exige conferência no banco e justificativa antes de liberar', async () => {
    const onRelease = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<BoletoReleaseAction {...props} onRelease={onRelease} />);
    await user.click(screen.getByRole('button', { name: 'Liberar nova tentativa' }));
    expect(screen.getByText(/cobrança duplicada/)).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: 'Confirmar liberação' });
    expect(submit).toBeDisabled();
    const reason = screen.getByLabelText('Justificativa da liberação');
    await user.type(reason, '   ');
    await user.click(screen.getByRole('checkbox'));
    expect(submit).toBeDisabled();
    await user.type(reason, 'Conferido no banco às 10h: boleto ausente.  ');
    await user.click(screen.getByRole('checkbox'));
    expect(submit).toBeDisabled();
    fireEvent.submit(reason.closest('form')!);
    expect(onRelease).not.toHaveBeenCalled();
    await user.click(screen.getByRole('checkbox'));
    await user.click(submit);
    await waitFor(() => expect(onRelease).toHaveBeenCalledExactlyOnceWith('Conferido no banco às 10h: boleto ausente.'));
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('permite desistir sem liberar e pede uma nova confirmação ao reabrir', async () => {
    const onRelease = vi.fn();
    const user = userEvent.setup();
    render(<BoletoReleaseAction {...props} onRelease={onRelease} />);
    await user.click(screen.getByRole('button', { name: 'Liberar nova tentativa' }));
    await user.click(screen.getByRole('checkbox'));
    await user.type(screen.getByLabelText('Justificativa da liberação'), 'Conferido');
    await user.click(screen.getByRole('button', { name: 'Voltar' }));
    expect(onRelease).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Liberar nova tentativa' }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByLabelText('Justificativa da liberação')).toHaveValue('');
  });

  it('impede envio duplicado e mantém a mensagem de erro da API no formulário', async () => {
    let reject!: (error: Error) => void;
    const onRelease = vi.fn(() => new Promise<void>((_, fail) => { reject = fail; }));
    const user = userEvent.setup();
    render(<BoletoReleaseAction {...props} onRelease={onRelease} />);
    await user.click(screen.getByRole('button', { name: 'Liberar nova tentativa' }));
    await user.click(screen.getByRole('checkbox'));
    const reason = screen.getByLabelText('Justificativa da liberação');
    await user.type(reason, 'Boleto ausente na conferência');
    await user.dblClick(screen.getByRole('button', { name: 'Confirmar liberação' }));
    fireEvent.submit(reason.closest('form')!);
    expect(onRelease).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Voltar' })).toBeDisabled();
    reject(new Error('O título já foi registrado. Atualize a consulta.'));
    expect(await screen.findByRole('alert')).toHaveTextContent('O título já foi registrado. Atualize a consulta.');
    expect(screen.getByRole('button', { name: 'Confirmar liberação' })).toBeEnabled();
  });
});
