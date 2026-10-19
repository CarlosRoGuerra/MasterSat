import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FechamentoPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'financeiro' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));

const fee = { type: 'taxa_desinstalacao', event_id: 91, client_id: 7, client_name: 'Cliente de teste',
  payer_client_id: 7, payer_name: 'Cliente de teste', client_type: 'pf', vehicle_plate: 'ABC1234',
  uninstall_date: '2026-10-16', fee_amount: 160, deferred: false, aggregation_total: 160,
  skipped: false, skip_reason: null };
let active: boolean;
let smallFee: boolean;
let deferred: boolean;
function simulation() {
  const fees = [
    ...(active ? [{ ...fee, deferred }] : []),
    ...(smallFee ? [{ ...fee, event_id: 92, fee_amount: 3, deferred: !active, aggregation_total: active ? 163 : 3 }] : []),
  ];
  const total = fees.filter(item => !item.deferred).reduce((sum, item) => sum + item.fee_amount, 0);
  return { reference_month: '10/2026', billing_month: '11/2026', total_contracts: 0, to_generate: 0,
    already_generated: 0, total_amount: 0, items: [], uninstall_events: fees,
    total_uninstall_fees: total, charge_items: [], total_services: 0, grand_total: total, payer_boleto_formats: { 7: 'unico' } };
}
const excluded = { event_id: 91, status: 'canceled', message: 'Taxa excluída deste fechamento e dos próximos. O histórico foi preservado.' };
const writes = () => api.fetch.mock.calls.filter(([, options]) => options?.method === 'POST');

beforeEach(() => {
  vi.resetAllMocks();
  active = true;
  smallFee = false;
  deferred = false;
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
    if (path.startsWith('/billing-closure/simulate?')) return simulation();
    if (path === '/billing-closure/uninstall-events/91/exclude' && options.method === 'POST') {
      active = false;
      return excluded;
    }
    throw new Error(`Requisição inesperada: ${path}`);
  });
});
afterEach(cleanup);

async function openPreview() {
  const user = userEvent.setup();
  const { container } = render(<FechamentoPage />);
  fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-10' } });
  await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
  await screen.findByRole('button', { name: 'Excluir taxa de desinstalação #91' });
  return user;
}
async function openExclusion(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Excluir taxa de desinstalação #91' }));
  return within(screen.getByRole('dialog', { name: 'Excluir taxa do fechamento' }));
}

describe('excluir taxa de desinstalação na prévia do fechamento', () => {
  it('mostra cliente/placa/valor e exige motivo antes de confirmar; desistir preserva a taxa', async () => {
    const user = await openPreview();
    const dialog = await openExclusion(user);
    expect(dialog.getByText(/Cliente de teste · ABC1234 · R\$/)).toBeInTheDocument();
    expect(dialog.getByText(/já foi recebida por boleto simples/)).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Voltar' }));
    expect(screen.getByRole('button', { name: 'Excluir taxa de desinstalação #91' })).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it('exclui com motivo e atualiza totais, título previsto e a possibilidade de gerar', async () => {
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), '  Já recebida pelo boleto simples  ');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('Taxa excluída deste fechamento e dos próximos');
    expect(screen.queryByRole('button', { name: 'Excluir taxa de desinstalação #91' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
    expect(screen.queryByText('Títulos previstos (1)')).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(writes()[0][0]).toBe('/billing-closure/uninstall-events/91/exclude');
    expect(JSON.parse(writes()[0][1].body)).toEqual({ reason: 'Já recebida pelo boleto simples' });
    const previews = api.fetch.mock.calls.filter(([path]) => path.startsWith('/billing-closure/simulate?'));
    expect(previews).toHaveLength(2);
    expect(previews[0][0]).toBe(previews[1][0]);
    expect(previews[1][0]).toContain('service_month=2026-10');
    await user.click(screen.getByRole('button', { name: 'Alterar escopo' }));
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(screen.queryByRole('button', { name: 'Excluir taxa de desinstalação #91' })).not.toBeInTheDocument();
  });

  it('recalcula a acumulação das taxas restantes do mesmo cliente', async () => {
    smallFee = true;
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), 'Taxa já paga');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('Aguardando acumulação')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Excluir taxa de desinstalação #92' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
  });

  it('permite excluir também uma taxa que estava aguardando acumulação', async () => {
    deferred = true;
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), 'Já recebida diretamente');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(active).toBe(false);
  });

  it('mantém motivo, taxa e erro visíveis se a API recusar uma taxa já faturada', async () => {
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path, options) => {
      if (path.endsWith('/exclude')) throw new Error('Esta taxa já foi faturada. Confira a cobrança no Financeiro.');
      return original(path, options);
    });
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), 'Já paga pelo boleto simples');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('Esta taxa já foi faturada.');
    expect(dialog.getByRole('textbox')).toHaveValue('Já paga pelo boleto simples');
    expect(active).toBe(true);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('informa exclusão concluída se o recálculo falhar e bloqueia gerar uma prévia antiga', async () => {
    const original = api.fetch.getMockImplementation()!;
    let reads = 0;
    api.fetch.mockImplementation(async (path, options) => {
      if (path.startsWith('/billing-closure/simulate?') && ++reads === 2) throw new Error('Falha na rede');
      return original(path, options);
    });
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), 'Já paga pelo boleto simples');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('Taxa excluída');
    expect(screen.getByText(/A taxa foi excluída, mas não foi possível atualizar/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Simular fechamento' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Confirmar e gerar/ })).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });

  it('bloqueia edição, fechamento e reenvio enquanto a exclusão está em andamento', async () => {
    const original = api.fetch.getMockImplementation()!;
    let finish!: (result: typeof excluded) => void;
    api.fetch.mockImplementation(async (path, options) => path.endsWith('/exclude')
      ? new Promise(resolve => { finish = resolve; }) : original(path, options));
    const user = await openPreview();
    const dialog = await openExclusion(user);
    await user.type(dialog.getByRole('textbox', { name: /Motivo da exclusão/ }), 'Já paga pelo boleto simples');
    await user.click(dialog.getByRole('button', { name: 'Confirmar exclusão da taxa' }));
    expect(dialog.getByRole('button', { name: 'Excluindo…' })).toBeDisabled();
    expect(dialog.getByRole('textbox')).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Voltar' })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar' }));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Excluir taxa do fechamento' })).toBeInTheDocument();
    active = false;
    finish(excluded);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(writes()).toHaveLength(1);
  });
});
