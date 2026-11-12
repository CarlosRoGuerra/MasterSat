import type { ReactNode } from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FinanceiroPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'financeiro' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('@/lib/arquivo', () => ({ entregarArquivo: vi.fn() }));
vi.mock('next/navigation', () => {
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useRouter: () => router };
});

const cliente = { id: 7, name: 'JACKSON RONALDO LARSEN', cpf_cnpj: '93677464900' };
const contrato = (id: number, plate: string) => ({ id, client_id: 7, plan_id: 1, status: 'ativo', start_date: '2026-01-01',
  plan_name: 'MENSALIDADE 64,99', vehicle_plate: plate, monthly_value: 64.99, open_billings: 0 });

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, '', '/financeiro');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['%PDF-test']) }));
  api.list.mockResolvedValue([cliente]);
  api.fetch.mockImplementation(async (path: string) => {
    if (path === '/sicredi/status') return { habilitado: true };
    if (path.startsWith('/contracts?client_id=7')) return [contrato(81, 'RMO0H18'), contrato(82, 'RDU2G08')];
    if (path === '/billings/parcelar') return Array.from({ length: 12 }, (_, i) => ({ id: 100 + i }));
    if (path === '/sicredi/carne/lote') return { id: 5, ticket: 'carne-5', status: 'processing' };
    if (path === '/sicredi/lotes/carne-5') return { status: 'completed', total: 12, prontas: 12, parcelas: [] };
    if (path === '/billings/summary') return { active_plans: 0, active_contracts: 0, pending_billings: 0,
      overdue_billings: 0, pending_amount: 0, overdue_amount: 0, paid_this_month: 0 };
    if (path === '/ailos/status') return { cooperado_status: 'authorized' };
    if (path === '/boletos/canais') return null;
    return [];
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function abrirCarneDoPlano() {
  const user = userEvent.setup();
  render(<FinanceiroPage />);
  await user.click(screen.getByRole('button', { name: 'Gerar carnê' }));
  await user.type(screen.getByPlaceholderText('Busque o cliente pelo nome ou CPF/CNPJ'), 'JACK');
  await user.click(await screen.findByRole('button', { name: /JACKSON RONALDO LARSEN/ }));
  await user.click(screen.getByRole('button', { name: 'A partir do plano' }));
  await user.click(await screen.findByRole('radio', { name: /Registrar no Sicredi/ }));
  await screen.findByRole('checkbox', { name: /RMO0H18/ });
  return user;
}

describe('carnê do plano com vários veículos', () => {
  it('seleciona todos os veículos ativos e gera um carnê com a soma dos planos', async () => {
    const user = await abrirCarneDoPlano();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar todos os veículos ativos (2)' }));
    expect(screen.getByRole('checkbox', { name: /RMO0H18/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /RDU2G08/ })).toBeChecked();
    expect(screen.getByDisplayValue(/129,98 \(soma dos planos\)/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Gerar carnê (12 parcelas)' }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('com 2 veículos'));
    await waitFor(() => expect(api.fetch.mock.calls.some(([p]) => p === '/sicredi/carne/lote')).toBe(true));
    const [, opcoes] = api.fetch.mock.calls.find(([p]) => p === '/billings/parcelar')!;
    expect(JSON.parse(String(opcoes.body))).toMatchObject({
      contract_ids: [81, 82], num_parcelas: 12, valor_parcela: null, banco: 'sicredi', liberar_canceladas: false,
    });
    const [, lote] = api.fetch.mock.calls.find(([p]) => p === '/sicredi/carne/lote')!;
    expect(JSON.parse(String(lote.body)).billing_ids).toHaveLength(12);
  });

  it('com um veículo só mantém o valor editável da parcela', async () => {
    const user = await abrirCarneDoPlano();
    await user.click(screen.getByRole('checkbox', { name: /RDU2G08/ }));
    expect(screen.getByPlaceholderText('valor do plano')).toHaveValue('64.99');
    await user.clear(screen.getByPlaceholderText('valor do plano'));
    await user.type(screen.getByPlaceholderText('valor do plano'), '70');
    await user.click(screen.getByRole('button', { name: 'Gerar carnê (12 parcelas)' }));
    await waitFor(() => expect(api.fetch.mock.calls.some(([p]) => p === '/billings/parcelar')).toBe(true));
    const [, opcoes] = api.fetch.mock.calls.find(([p]) => p === '/billings/parcelar')!;
    expect(JSON.parse(String(opcoes.body))).toMatchObject({ contract_ids: [82], valor_parcela: 70 });
  });
});
