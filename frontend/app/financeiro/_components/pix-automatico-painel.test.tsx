import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { PixAutomaticoPainel, type PixPainel } from './pix-automatico-painel';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const painel: PixPainel = {
  ambiente: 'homologacao', hoje: '2026-11-16', habilitado: true, escrita_habilitada: true,
  resumo: { autorizacoes_pendentes: 1, debitos_nao_realizados: 1, total_aguardando_confirmacao: '149.90',
    clientes_a_contatar: 2, falhas_integracao: 0, acoes_necessarias: 3 },
  linhas: [
    { client_id: 1, cliente: 'Cliente A', billing_id: 10, cobranca_pix_id: 5, vencimento: '2026-12-15', valor: '99.90',
      situacao: 'autorizacao_pendente', situacao_label: 'Autorização pendente do cliente', acao: 'contatar_cliente',
      acao_label: 'Contatar cliente', gravidade: 'media', detalhe: 'Convite enviado por e-mail.', convite_status: 'enviado' },
    { client_id: 2, cliente: 'Cliente B', billing_id: 11, cobranca_pix_id: 6, vencimento: '2026-11-10', valor: '149.90',
      situacao: 'debito_nao_realizado', situacao_label: 'Débito não realizado', acao: 'contatar_cliente',
      acao_label: 'Contatar cliente', gravidade: 'alta', detalhe: null, convite_status: null },
  ],
};

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockImplementation(async (path: string) => {
    if (path.startsWith('/sicredi-pix/painel')) return painel;
    return { conexao: { configurado: true, escrita_habilitada: true, ambiente: 'homologacao' }, recorrencia: null,
      cobrancas: [], pendentes_adesao: 0 };
  });
});

describe('painel Pix Automático do Financeiro', () => {
  it('mostra o contador de ações e a tabela com situação e próxima ação', async () => {
    render(<PixAutomaticoPainel token="t" />);
    expect(await screen.findByLabelText('3 ação(ões) necessária(s)')).toHaveTextContent('3');
    fireEvent.click(screen.getByRole('button', { name: /Pix Automático/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Débitos não realizados')).toBeInTheDocument();
    expect(within(dialog).getByText('Clientes a contatar')).toBeInTheDocument();
    expect(within(dialog).getByText('Aguardando confirmação').nextElementSibling).toHaveTextContent('149,90');
    expect(within(dialog).getByText('Débito não realizado')).toBeInTheDocument();
    expect(within(dialog).getByText('Autorização pendente do cliente')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Contatar cliente')).toHaveLength(2);
    expect(vi.mocked(apiFetch).mock.calls.every(([, init]) => !init?.method)).toBe(true);  // somente leitura
  });

  it('inclui agendados e pagos sob demanda e abre o cliente no próprio painel', async () => {
    render(<PixAutomaticoPainel token="t" />);
    fireEvent.click(await screen.findByRole('button', { name: /Pix Automático/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText('Mostrar também agendados e pagos'));
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([p]) => p === '/sicredi-pix/painel?incluir_resolvidos=true')).toBe(true));
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Abrir' })[1]);
    expect(await within(dialog).findByText('Cliente B')).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(apiFetch)).toHaveBeenCalledWith('/sicredi-pix/clientes/2', {}, 't'));
    expect(within(dialog).getByRole('button', { name: /Voltar ao painel/ })).toBeInTheDocument();
  });

  it('erro de carga é exibido sem esconder o botão', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error('Falha ao consultar.'));
    render(<PixAutomaticoPainel token="t" />);
    fireEvent.click(await screen.findByRole('button', { name: /Pix Automático/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Falha ao consultar.');
  });
});
