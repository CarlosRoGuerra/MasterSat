import type { ReactNode } from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FinanceiroPage from './page';
import type { BillingBatchResult } from '@/lib/billing-batch';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'financeiro' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useRouter: () => router };
});

const billing = { id: 10, client_id: 7, billing_type: 'avulsa', title: 'Cobrança de teste',
  due_date: '2026-10-15', amount: 64.99, status: 'pendente', overdue_days: 0 };
let wallet: typeof billing[];

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, '', '/financeiro');
  wallet = [{ ...billing }];
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
    if (path.startsWith('/billings?')) return wallet;
    if (path === '/billings/lote/acoes') {
      const body = JSON.parse(String(options.body));
      const ids = new Set<number>(body.billing_ids);
      const result: BillingBatchResult = { action: body.action, simulacao: body.simular,
        total_enviados: ids.size, aplicados: ids.size, ignorados: 0, revisao: 'b'.repeat(64),
        itens: wallet.filter((item) => ids.has(item.id)).map((item) => ({ billing_id: item.id,
          status: item.status, situacao: body.simular ? 'aplicavel' : 'aplicado',
          baixa_bancaria_pendente: false, libera_competencia: false })) };
      if (!body.simular) wallet = body.action === 'excluir' ? wallet.filter((item) => !ids.has(item.id))
        : wallet.map((item) => ids.has(item.id) ? { ...item, status: 'cancelada' } : item);
      return result;
    }
    if (path === '/billings/summary') return { active_plans: 0, active_contracts: 0,
      pending_billings: 0, overdue_billings: 0, pending_amount: 0, overdue_amount: 0, paid_this_month: 0 };
    if (path === '/ailos/status') return { cooperado_status: 'authorized' };
    if (path === '/ailos/conciliacao') return { carteira_monitorada: 0, nunca_consultados: 0,
      atraso_max_horas: 0, janela_estimada_horas: 0, desfecho_desconhecido: 0, baixa_pendente: 0, alerta_atraso: false };
    if (path === '/boletos/canais') return null;
    return [];
  });
});
afterEach(cleanup);

async function openWallet() {
  const user = userEvent.setup();
  render(<FinanceiroPage />);
  await user.click(screen.getByRole('button', { name: 'Alterar situação boleto em lote' }));
  await screen.findByRole('checkbox', { name: 'Selecionar boletos desta página' });
  return user;
}

describe('ações em lote no Financeiro', () => {
  it.each(['cancelar', 'excluir'] as const)('confere e aplica %s pelo menu e atualiza a carteira', async (action) => {
    if (action === 'excluir') wallet[0].status = 'cancelada';
    const user = await openWallet();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar boleto #10' }));
    if (action === 'excluir') expect(screen.getByRole('button', { name: 'Receber em lote' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: action === 'cancelar' ? 'Cancelar em lote' : 'Excluir em lote' }));
    expect(screen.queryByRole('dialog', { name: 'Alterar situação em lote' })).not.toBeInTheDocument();
    await screen.findByText('1 apto(s) · 0 ignorado(s) · 1 selecionado(s)');
    await user.type(screen.getByRole('textbox', { name: /Justificativa/ }), 'Solicitação do cliente');
    await user.click(screen.getByRole('button', { name: `${action === 'cancelar' ? 'Cancelar' : 'Excluir'} 1 boleto(s)` }));
    await user.click(await screen.findByRole('button', { name: 'Fechar resultado' }));
    const list = within(screen.getByRole('dialog', { name: 'Alterar situação em lote' }));
    if (action === 'cancelar') expect(await list.findByRole('cell', { name: 'Cancelada' })).toBeInTheDocument();
    else expect(await list.findByText('Nenhuma cobrança encontrada')).toBeInTheDocument();
    expect(list.queryByText('1 selecionada(s)')).not.toBeInTheDocument();
    const calls = api.fetch.mock.calls.filter(([path]) => path === '/billings/lote/acoes');
    expect(calls).toHaveLength(2);
    expect(JSON.parse(calls[1][1].body)).toMatchObject({ action, billing_ids: [10], simular: false,
      revisao: 'b'.repeat(64), reason: 'Solicitação do cliente' });
  });

  it('preserva a seleção de outra página e não seleciona boletos pagos', async () => {
    wallet = Array.from({ length: 26 }, (_, index) => ({ ...billing, id: index + 1,
      status: index === 1 ? 'paga' : index === 0 ? 'cancelada' : 'pendente' }));
    const user = await openWallet();
    expect(screen.queryByRole('checkbox', { name: 'Selecionar boleto #2' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar boletos desta página' }));
    expect(screen.getByText('24 selecionada(s)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Próxima página' }));
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar boletos desta página' }));
    expect(screen.getByText('25 selecionada(s)')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar boletos desta página' }));
    expect(screen.getByText('24 selecionada(s)')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Selecionar boleto #26' })).not.toBeChecked();
  });
});
