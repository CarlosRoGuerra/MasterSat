import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CorrigirVencimentoAction } from './corrigir-vencimento-action';
import type { TituloBancario } from '@/lib/titulo-bancario';

const props = {
  billingId: 38734,
  billingStatus: 'pendente',
  dueDate: '2026-10-28',
  titulo: { estado: 'registrado' as const },
  canEdit: true,
};

describe('Correção de vencimento de boleto registrado', () => {
  it('só aparece para cobrança em aberto com título no banco e perfil que edita', () => {
    const onCorrigir = vi.fn();
    const { rerender } = render(<CorrigirVencimentoAction {...props} canEdit={false} onCorrigir={onCorrigir} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    for (const estado of ['sem_titulo', 'em_registro', 'desfecho_desconhecido', 'remessa_cnab'] as TituloBancario['estado'][]) {
      rerender(<CorrigirVencimentoAction {...props} titulo={{ estado }} onCorrigir={onCorrigir} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    }
    for (const billingStatus of ['paga', 'cancelada']) {
      rerender(<CorrigirVencimentoAction {...props} billingStatus={billingStatus} onCorrigir={onCorrigir} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    }
    rerender(<CorrigirVencimentoAction {...props} titulo={{ estado: 'baixado' }} onCorrigir={onCorrigir} />);
    expect(screen.getByRole('button', { name: 'Corrigir vencimento' })).toBeInTheDocument();
  });

  it('exige data diferente, justificativa e ciência do boleto antigo', async () => {
    const onCorrigir = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<CorrigirVencimentoAction {...props} onCorrigir={onCorrigir} />);
    await user.click(screen.getByRole('button', { name: 'Corrigir vencimento' }));
    expect(screen.getByText(/baixa pendente/)).toBeInTheDocument();
    const submit = screen.getAllByRole('button', { name: 'Corrigir vencimento' }).at(-1)!;
    expect(submit).toBeDisabled();

    const data = screen.getByLabelText('Vencimento correto');
    await user.type(data, '2026-10-28');  // igual à atual
    await user.type(screen.getByLabelText('Justificativa'), 'dia do cliente é 10');
    await user.click(screen.getByRole('checkbox'));
    expect(submit).toBeDisabled();

    await user.clear(data);
    await user.type(data, '2026-10-10');
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onCorrigir).toHaveBeenCalledWith('2026-10-10', 'dia do cliente é 10');
  });

  it('mostra o erro do servidor e mantém o formulário aberto', async () => {
    const onCorrigir = vi.fn().mockRejectedValue(new Error('Registro em andamento na Ailos'));
    const user = userEvent.setup();
    render(<CorrigirVencimentoAction {...props} onCorrigir={onCorrigir} />);
    await user.click(screen.getByRole('button', { name: 'Corrigir vencimento' }));
    await user.type(screen.getByLabelText('Vencimento correto'), '2026-10-10');
    await user.type(screen.getByLabelText('Justificativa'), 'dia 10');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getAllByRole('button', { name: 'Corrigir vencimento' }).at(-1)!);
    expect(await screen.findByRole('alert')).toHaveTextContent('Registro em andamento na Ailos');
    expect(screen.getByLabelText('Vencimento correto')).toBeInTheDocument();
  });
});
