import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BillingBatchModal } from './billing-batch-modal';
import type { BillingBatchAction, BillingBatchResult } from '@/lib/billing-batch';

const api = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch: api }));

function response(action: BillingBatchAction = 'cancelar', extra: Partial<BillingBatchResult['itens'][number]> = {}): BillingBatchResult {
  return { simulacao: true, action, total_enviados: 2, aplicados: 1, ignorados: 1, revisao: 'a'.repeat(64), itens: [
    { billing_id: 10, title: 'Mensalidade', status: action === 'cancelar' ? 'pendente' : 'cancelada',
      situacao: 'aplicavel', baixa_bancaria_pendente: false, libera_competencia: false, ...extra },
    { billing_id: 11, status: 'paga', situacao: 'ignorado', motivo: 'Cobrança paga: preserve o histórico.',
      baixa_bancaria_pendente: false, libera_competencia: false },
  ] };
}
function applied(review: BillingBatchResult): BillingBatchResult {
  return { ...review, simulacao: false, itens: review.itens.map((item) => item.situacao === 'aplicavel' ? { ...item, situacao: 'aplicado' } : item) };
}
function renderModal(action: BillingBatchAction = 'cancelar') {
  const onClose = vi.fn();
  const onCompleted = vi.fn().mockResolvedValue(undefined);
  render(<BillingBatchModal action={action} ids={[10, 11]} token="test-token" onClose={onClose} onCompleted={onCompleted} />);
  return { onClose, onCompleted, user: userEvent.setup() };
}
beforeEach(() => {
  vi.resetAllMocks();
  api.mockImplementation(async (_path, options) => {
    const body = JSON.parse(options.body);
    const review = response(body.action);
    return body.simular ? review : applied(review);
  });
});
afterEach(cleanup);

describe('conferência e resultado do lote de boletos', () => {
  it('confere sem gravar, exibe o motivo do bloqueio e exige justificativa', async () => {
    renderModal();
    expect(await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)')).toBeInTheDocument();
    expect(screen.getByText(/Ignorado: Cobrança paga/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancelar 1 boleto(s)' })).toBeDisabled();
    expect(JSON.parse(api.mock.calls[0][1].body)).toEqual({ action: 'cancelar', billing_ids: [10, 11], simular: true });
    expect(api).toHaveBeenCalledTimes(1);
  });

  it.each(['cancelar', 'excluir'] as const)('aplica %s com a revisão e mantém o resultado por boleto', async (action) => {
    const { user, onCompleted } = renderModal(action);
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), '  Solicitação do cliente  ');
    await user.click(screen.getByRole('button', { name: `${action === 'cancelar' ? 'Cancelar' : 'Excluir'} 1 boleto(s)` }));
    expect(await screen.findByRole('status')).toHaveTextContent(`1 boleto(s) ${action === 'cancelar' ? 'cancelado(s)' : 'excluído(s)'}`);
    expect(onCompleted).toHaveBeenCalledWith(expect.objectContaining({ simulacao: false, action }));
    expect(JSON.parse(api.mock.calls[1][1].body)).toEqual({ action, billing_ids: [10, 11], simular: false,
      revisao: 'a'.repeat(64), reason: 'Solicitação do cliente', confirmar_boleto_ailos: false, confirmar_liberacao_competencia: false });
    expect(screen.getByRole('button', { name: 'Fechar resultado' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /[CE].* 1 boleto/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Ignorado: Cobrança paga/)).toBeInTheDocument();
  });

  it('exige ciência da baixa bancária antes de cancelar um título ativo', async () => {
    const review = response('cancelar', { baixa_bancaria_pendente: true, nosso_numero: 'NN123' });
    api.mockImplementation(async (_path, options) => JSON.parse(options.body).simular ? review : applied(review));
    const { user } = renderModal();
    await screen.findByText('Nosso número: NN123');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Solicitado pelo cliente');
    const button = screen.getByRole('button', { name: 'Cancelar 1 boleto(s)' });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /providenciar a baixa/ }));
    await user.click(button);
    await screen.findByRole('button', { name: 'Fechar resultado' });
    expect(JSON.parse(api.mock.calls[1][1].body).confirmar_boleto_ailos).toBe(true);
    expect(screen.getByText(/continuam pagáveis no banco/)).toBeInTheDocument();
  });

  it('avisa e exige confirmação da liberação do mês na exclusão', async () => {
    const review = response('excluir', { libera_competencia: true });
    api.mockImplementation(async (_path, options) => JSON.parse(options.body).simular ? review : applied(review));
    const { user } = renderModal('excluir');
    await screen.findByText(/libera os meses para nova cobrança/);
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Correção da carteira');
    expect(screen.getByRole('button', { name: 'Excluir 1 boleto(s)' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /Confirmo a liberação dos meses/ }));
    await user.click(screen.getByRole('button', { name: 'Excluir 1 boleto(s)' }));
    await screen.findByRole('button', { name: 'Fechar resultado' });
    expect(JSON.parse(api.mock.calls[1][1].body).confirmar_liberacao_competencia).toBe(true);
  });

  it('permite sair da conferência sem enviar a aplicação', async () => {
    const { user, onClose } = renderModal();
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    await user.click(screen.getByRole('button', { name: 'Voltar' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('bloqueia fechamento e reenvio enquanto aplica e atualiza a carteira', async () => {
    let finish!: (result: BillingBatchResult) => void;
    api.mockImplementation(async (_path, options) => JSON.parse(options.body).simular ? response()
      : new Promise<BillingBatchResult>((resolve) => { finish = resolve; }));
    const { user, onClose, onCompleted } = renderModal();
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    await user.click(screen.getByRole('button', { name: 'Cancelar 1 boleto(s)' }));
    expect(screen.getByRole('button', { name: 'Aplicando…' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: /Justificativa/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Fechar' }));
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    finish(applied(response()));
    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('exige nova conferência quando a situação mudou no servidor', async () => {
    api.mockImplementation(async (_path, options) => {
      if (JSON.parse(options.body).simular) return response();
      throw Object.assign(new Error('A seleção mudou. Confira novamente.'), { detail: { code: 'revisao_desatualizada' } });
    });
    const { user } = renderModal();
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    await user.click(screen.getByRole('button', { name: 'Cancelar 1 boleto(s)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('A seleção mudou.');
    expect(screen.getByRole('button', { name: 'Cancelar 0 boleto(s)' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Conferir novamente' }));
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    expect(screen.getByRole('textbox', { name: /Justificativa/ })).toHaveValue('Negociação');
    expect(api).toHaveBeenCalledTimes(3);
  });

  it('mostra falha de atualização separadamente da operação já concluída', async () => {
    const { user, onCompleted } = renderModal();
    onCompleted.mockRejectedValue(new Error('Rede indisponível'));
    await screen.findByText('1 apto(s) · 1 ignorado(s) · 2 selecionado(s)');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    await user.click(screen.getByRole('button', { name: 'Cancelar 1 boleto(s)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Operação concluída. Falha ao atualizar a tela');
    expect(screen.getByRole('status')).toHaveTextContent('1 boleto(s) cancelado(s)');
    expect(screen.getByRole('button', { name: 'Fechar resultado' })).toBeInTheDocument();
  });

  it('não oferece aplicação quando todos os boletos estão bloqueados', async () => {
    const review = response();
    review.aplicados = 0; review.ignorados = 2;
    review.itens[0] = { ...review.itens[0], situacao: 'ignorado', motivo: 'Cobrança já cancelada.' };
    api.mockResolvedValue(review);
    renderModal();
    await screen.findByText('0 apto(s) · 2 ignorado(s) · 2 selecionado(s)');
    expect(screen.getByRole('button', { name: 'Cancelar 0 boleto(s)' })).toBeDisabled();
  });
});
