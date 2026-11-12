import type { ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FechamentoPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn(), delivery: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'financeiro' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/app/clientes/_components/closure-delivery-modal', () => ({ ClosureDeliveryModal: (props: object) => { api.delivery(props); return null; } }));

function simulation(count: number, service = false) {
  const items = Array.from({ length: count }, (_, offset) => {
    const due = new Date(Date.UTC(2026, 11 + offset, 10)).toISOString().slice(0, 10);
    return { type: 'recorrente', contract_id: 668, client_id: 115, payer_client_id: 115,
      client_name: 'Cliente do carne', payer_name: 'Cliente do carne', client_type: 'pf',
      vehicle_plate: 'ABC1234', tracker_id: 314, tracker_imei: null, plan_name: 'Mensalidade', plan_price: 64.99,
      billing_amount: 64.99, total_first_billing: 64.99, is_prorata: false,
      prorated_days: 0, days_in_month: 0, first_month_charges: [], period_label: due.slice(0, 7),
      due_date: due, already_generated: false, billing_day: 10,
      ...(count > 1 ? { installment_number: offset + 1, installment_total: count } : {}),
    };
  });
  return { reference_month: '11/2026', billing_month: '12/2026', total_contracts: 1, to_generate: count,
    carne_installments: count > 1 ? count : null, already_generated: 0, total_amount: 64.99 * count,
    items, uninstall_events: [], total_uninstall_fees: 0, total_services: service ? 10 : 0,
    charge_items: service ? [{ type: 'servico', item_id: 91, client_id: 115, payer_client_id: 115,
      client_name: 'Cliente do carne', payer_name: 'Cliente do carne', client_type: 'pf', vehicle_plate: null,
      title: 'Servico do primeiro mes', quantity: 1, unit_price: 10, installment_count: 1, generated_count: 0,
      remaining_installments: 1, per_installment_amount: 10, amount_to_generate: 10, total_remaining: 10,
      start_date: '2026-11-01' }] : [],
    grand_total: 64.99 * count + (service ? 10 : 0), payer_boleto_formats: { 115: 'unico' } };
}

let extraService = false;
beforeEach(() => {
  vi.resetAllMocks();
  extraService = false;
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string) => {
    const params = new URLSearchParams(path.split('?')[1]);
    if (path.startsWith('/billing-closure/simulate?')) return simulation(Number(params.get('carne_installments') || 1), extraService);
    if (path.startsWith('/billing-closure/generate?')) return { status: 'completed', closure_batch_id: 30,
      reference_month: '2026-11', reference_month_label: '11/2026', carne_installments: 12,
      generated: 12, payment_titles_generated: 12, grand_total: 779.88, uninstall_events_processed: 0,
      uninstall_fees_generated: 0, uninstall_fees_deferred: 0, services_generated: 0, total_services_amount: 0 };
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

async function setup() {
  const user = userEvent.setup();
  const { container } = render(<FechamentoPage />);
  fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-11' } });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Forma de cobrança' }), 'carne_sicredi');
  return user;
}

describe('fechamento do carne Sicredi', () => {
  it('sugere o mês atual para carnê e o anterior para mensal, preservando o mês escolhido manualmente', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-12T15:00:00-03:00'));
    const user = userEvent.setup();
    const { container } = render(<FechamentoPage />);
    const month = container.querySelector('input[type="month"]')!;
    expect(month).toHaveValue('2026-10');
    await user.click(screen.getByRole('button', { name: 'Carnês Sicredi (12 meses)' }));
    expect(month).toHaveValue('2026-11');
    await user.click(screen.getByRole('button', { name: 'Boletos mensais' }));
    expect(month).toHaveValue('2026-10');
    fireEvent.change(month, { target: { value: '2026-09' } });
    await user.click(screen.getByRole('button', { name: 'Carnês Sicredi (12 meses)' }));
    expect(month).toHaveValue('2026-09');
    await user.click(screen.getByRole('button', { name: 'Boletos mensais' }));
    expect(month).toHaveValue('2026-09');
  });

  it('agrupa o responsável com as doze parcelas, valores e vencimentos para conferência', async () => {
    const user = await setup();
    expect(screen.getByRole('spinbutton', { name: 'Parcelas do carnê' })).toHaveValue(12);
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText('Títulos previstos (12)')).toBeInTheDocument();
    const carne = within(screen.getByRole('region', { name: 'Carnê de Cliente do carne' }));
    expect(carne.getByText('Parcela 1/12 · 10/12/2026')).toBeInTheDocument();
    expect(carne.getByText('Parcela 12/12 · 10/11/2027')).toBeInTheDocument();
    expect(carne.getByText('12 parcelas mensais · 1 veículo(s) / contrato(s)')).toBeInTheDocument();
    expect(carne.getByText('Primeiro vencimento: 10/12/2026 · Último vencimento: 10/11/2027')).toBeInTheDocument();
    expect(carne.getByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeEnabled();
    expect(api.fetch.mock.calls[0][0]).toContain('service_month=2026-11');
    expect(api.fetch.mock.calls[0][0]).toContain('carne_installments=12');
    expect(api.fetch.mock.calls.every(([path]) => path.startsWith('/billing-closure/simulate?'))).toBe(true);
  });

  it('sends the same installment scope and exact contract selection to generate', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await user.click(await screen.findByRole('button', { name: 'Confirmar e gerar: 12 título(s)' }));
    await screen.findByText('Lote de fechamento #30');
    const writes = api.fetch.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(writes).toHaveLength(1);
    const params = new URLSearchParams(writes[0][0].split('?')[1]);
    expect(params.get('carne_installments')).toBe('12');
    expect(params.getAll('contract_ids')).toEqual(['668']);
    expect(params.get('forma_cobranca')).toBe('carne_sicredi');
    expect(params.get('uninstall_event_ids')).toBe('-1');
    expect(params.get('charge_item_ids')).toBe('-1');
  });

  it('keeps a service in the first month rather than combining all months into one title', async () => {
    extraService = true;
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText('Títulos previstos (12)')).toBeInTheDocument();
    const firstTitle = screen.getByText('Parcela 1/12 · 10/12/2026').closest('div')!;
    expect(within(firstTitle).getByText(/74,99/)).toBeInTheDocument();
  });

  it('offers the twelve-month booklet closing as its own closing type', async () => {
    const user = userEvent.setup();
    const { container } = render(<FechamentoPage />);
    fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-11' } });
    expect(screen.getByRole('button', { name: 'Boletos mensais' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Carnês Sicredi (12 meses)' }));
    expect(screen.getByRole('button', { name: 'Carnês Sicredi (12 meses)' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('combobox', { name: 'Forma de cobrança' })).toHaveValue('carne_sicredi');
    expect(screen.getByRole('spinbutton', { name: 'Parcelas do carnê' })).toHaveValue(12);
    expect(screen.getByText(/Todos = PF e PJ com Carnê Sicredi/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText('Títulos previstos (12)')).toBeInTheDocument();
    const params = new URLSearchParams(api.fetch.mock.calls[0][0].split('?')[1]);
    expect(params.get('forma_cobranca')).toBe('carne_sicredi');
    expect(params.get('carne_installments')).toBe('12');
  });

  it('returns to the monthly closing without booklet installments', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Boletos mensais' }));
    expect(screen.getByRole('combobox', { name: 'Forma de cobrança' })).toHaveValue('');
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  });

  it('validates the installment count and keeps the monthly path unchanged', async () => {
    const user = await setup();
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1' } });
    expect(screen.getByRole('button', { name: 'Simular fechamento' })).toBeDisabled();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Forma de cobrança' }), 'boleto_mensal');
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await waitFor(() => expect(screen.getByText('Títulos previstos (1)')).toBeInTheDocument());
    expect(api.fetch.mock.calls[0][0]).not.toContain('carne_installments');
  });

  it('preserva dois títulos individuais e a tabela de veículos no fechamento mensal', async () => {
    const data = simulation(1);
    data.items.push(...data.items.map(item => ({ ...item, contract_id: 669, tracker_id: 315, vehicle_plate: 'DEF5678' })));
    data.payer_boleto_formats[115] = 'individual';
    api.fetch.mockResolvedValueOnce(data);
    const user = userEvent.setup();
    render(<FechamentoPage />);
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByRole('heading', { name: 'Títulos previstos (2)' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Itens de mensalidade (2)' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /Carnê de/ })).not.toBeInTheDocument();
    expect(new URLSearchParams(api.fetch.mock.calls[0][0].split('?')[1]).has('carne_installments')).toBe(false);
  });

  it.each([
    ['Todos', 'all'], ['Pessoa Física', 'pf'], ['Pessoa Jurídica', 'pj'],
  ])('mantém o grupo %s restrito à forma Carnê Sicredi', async (label, filter) => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: label }));
    expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    const params = new URLSearchParams(api.fetch.mock.calls[0][0].split('?')[1]);
    expect(params.get('filter_type')).toBe(filter);
    expect(params.get('forma_cobranca')).toBe('carne_sicredi');
    expect(params.get('carne_installments')).toBe('12');
  });

  it('reúne dois veículos em doze títulos de 129,98 mesmo com formato individual no cadastro', async () => {
    const data = simulation(12);
    data.items.push(...data.items.map(item => ({ ...item, contract_id: 669, tracker_id: 315, vehicle_plate: 'DEF5678' })));
    data.total_contracts = 2;
    data.to_generate = 24;
    data.total_amount *= 2;
    data.grand_total *= 2;
    data.payer_boleto_formats[115] = 'individual';
    api.fetch.mockResolvedValueOnce(data);
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    const carne = within(screen.getByRole('region', { name: 'Carnê de Cliente do carne' }));
    expect(carne.getByText('12 parcelas mensais · 2 veículo(s) / contrato(s)')).toBeInTheDocument();
    expect(carne.getByText(/ABC1234 · Mensalidade/)).toBeInTheDocument();
    expect(carne.getByText(/DEF5678 · Mensalidade/)).toBeInTheDocument();
    expect(carne.getAllByText(/R\$\s*129,98/)).toHaveLength(12);
    expect(carne.getByRole('link', { name: 'Abrir cadastro de Cliente do carne' })).toHaveAttribute('href', '/clientes?focus=115');
    expect(carne.getByRole('link', { name: 'Abrir cadastro de Cliente do carne' })).toHaveAttribute('target', '_blank');
    expect(carne.getByRole('link', { name: 'Ajustar mensalidade de ABC1234' })).toHaveAttribute('href', '/rastreadores?focus=314');
    expect(carne.getByRole('link', { name: 'Ajustar mensalidade de DEF5678' })).toHaveAttribute('href', '/rastreadores?focus=315');
    await user.click(carne.getByRole('checkbox'));
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
    await user.click(carne.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' }));
    await screen.findByText('Lote de fechamento #30');
    const write = api.fetch.mock.calls.find(([, options]) => options?.method === 'POST')!;
    expect(new URLSearchParams(write[0].split('?')[1]).getAll('contract_ids')).toEqual(['668', '669']);
  });

  it.each([
    { days: [20, 10], expectedDay: 10 },
    { days: [10, 20], expectedDay: 10 },
    { days: [20, 20, 10], expectedDay: 20 },
    { days: [10, 20, 20], expectedDay: 20 },
  ])('usa vencimento mais frequente e desempata pelo mais cedo, independente da ordem $days', async ({ days, expectedDay }) => {
    const data = simulation(12, true);
    data.items = days.flatMap((day, index) => data.items.map(item => ({ ...item,
      contract_id: 668 + index, tracker_id: 314 + index, vehicle_plate: `ABC123${index}`,
      due_date: `${item.due_date.slice(0, 8)}${String(day).padStart(2, '0')}`, billing_day: day,
    })));
    data.total_contracts = days.length;
    data.to_generate = data.items.length;
    data.total_amount *= days.length;
    data.grand_total = data.total_amount + data.total_services;
    api.fetch.mockResolvedValueOnce(data);
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    const carne = within(screen.getByRole('region', { name: 'Carnê de Cliente do carne' }));
    expect(carne.getByText(`Primeiro vencimento: ${expectedDay}/12/2026 · Último vencimento: ${expectedDay}/11/2027`)).toBeInTheDocument();
    for (const [offset, item] of simulation(12).items.entries()) {
      const date = `${expectedDay}/${item.due_date.slice(5, 7)}/${item.due_date.slice(0, 4)}`;
      expect(carne.getByText(`Parcela ${offset + 1}/12 · ${date}`)).toBeInTheDocument();
    }
    const firstTitle = carne.getByText(`Parcela 1/12 · ${expectedDay}/12/2026`).closest('div')!;
    expect(within(firstTitle).getByText(days.length === 2 ? /139,98/ : /204,97/)).toBeInTheDocument();
    expect(api.fetch.mock.calls.every(([path]) => path.startsWith('/billing-closure/simulate?'))).toBe(true);
  });

  it('recarrega mensalidades após abrir cadastro, preserva a seleção válida e não gera ao atualizar', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    fireEvent.click(screen.getByRole('link', { name: 'Abrir cadastro de Cliente do carne' }));
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeDisabled();
    const updated = simulation(12);
    updated.items = updated.items.map(item => ({ ...item, plan_price: 79.99, billing_amount: 79.99, total_first_billing: 79.99 }));
    let resolveRefresh!: (value: typeof updated) => void;
    api.fetch.mockImplementationOnce(() => new Promise(resolve => { resolveRefresh = resolve; }));
    await user.click(screen.getByRole('button', { name: 'Atualizar prévia' }));
    expect(screen.getByText('Recarregando mensalidades e elegibilidade dos carnês…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeDisabled();
    await act(async () => { resolveRefresh(updated); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeEnabled());
    expect(screen.getByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' })).toBeChecked();
    expect(screen.getAllByText(/R\$\s*79,99/)).toHaveLength(13);
    expect(api.fetch.mock.calls.every(([path, options]) => path.startsWith('/billing-closure/simulate?') && !options?.method)).toBe(true);
    expect(api.fetch.mock.calls[0][0]).toBe(api.fetch.mock.calls[1][0]);
  });

  it('mostra falha no refresh e exige uma atualização bem sucedida antes de gerar', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    api.fetch.mockRejectedValueOnce(new Error('Falha ao consultar mensalidades atualizadas.'));
    await user.click(screen.getByRole('button', { name: 'Atualizar prévia' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Falha ao consultar mensalidades atualizadas.');
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Atualizar prévia' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeEnabled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(api.fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  });

  it('preserva responsáveis desmarcados e remove seleções que ficaram bloqueadas no refresh', async () => {
    const data = simulation(12);
    const second = data.items.map(item => ({ ...item, contract_id: 669, client_id: 116, payer_client_id: 116, client_name: 'Segundo cliente', payer_name: 'Segundo cliente' }));
    api.fetch.mockResolvedValueOnce({ ...data, items: [...data.items, ...second] });
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await user.click(await screen.findByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' }));
    const reason = 'Existe cobrança no próximo ciclo. Confira o responsável antes de renovar.';
    api.fetch.mockResolvedValueOnce({ ...data,
      items: [...data.items, ...second.map(item => ({ ...item, already_generated: true, carne_renewal_blocked: true, carne_renewal_reason: reason }))],
    });
    await user.click(screen.getByRole('button', { name: 'Atualizar prévia' }));
    await screen.findByText(reason);
    expect(screen.getByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' })).not.toBeChecked();
    expect(screen.queryByRole('checkbox', { name: 'Selecionar carnê de Segundo cliente' })).not.toBeInTheDocument();
    expect(screen.getByText('Títulos previstos (0)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
  });

  it('exige conferir todos os veículos quando um novo contrato aparece após a atualização', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await screen.findByText('Títulos previstos (12)');
    const data = simulation(12);
    api.fetch.mockResolvedValueOnce({ ...data, items: [...data.items, ...data.items.map(item => ({ ...item, contract_id: 669, vehicle_plate: 'DEF5678' }))] });
    await user.click(screen.getByRole('button', { name: 'Atualizar prévia' }));
    expect(await screen.findByText(/A lista de veículos mudou/)).toBeInTheDocument();
    const checkbox = screen.getByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' });
    expect(checkbox).toBePartiallyChecked();
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeDisabled();
    await user.click(checkbox);
    expect(checkbox).toBeChecked();
    expect(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' })).toBeEnabled();
    expect(screen.getAllByText(/R\$\s*129,98/)).toHaveLength(12);
  });

  it('desmarca o carnê inteiro, incluindo os serviços da primeira parcela', async () => {
    extraService = true;
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await user.click(await screen.findByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' }));
    expect(screen.getByText('Títulos previstos (0)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
    expect(api.fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  });

  it('mantém os serviços de pagador bloqueado fora dos títulos e da geração', async () => {
    const data = simulation(12, true);
    const reason = 'O responsável não possui ciclo elegível para renovação.';
    api.fetch.mockResolvedValueOnce({ ...data, total_services: 0,
      charge_items: data.charge_items.map(item => ({ ...item, payer_client_id: 116, payer_name: 'Responsável bloqueado', carne_renewal_blocked: true, carne_renewal_reason: reason })),
      carne_renewal_blocks: [{ payer_client_id: 116, payer_name: 'Responsável bloqueado', contract_ids: [], reason }],
    });
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByText('Títulos previstos (12)')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Selecionar carnê de Responsável bloqueado' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirmar e gerar: 12 título(s)' }));
    await screen.findByText('Lote de fechamento #30');
    const write = api.fetch.mock.calls.find(([, options]) => options?.method === 'POST')!;
    expect(new URLSearchParams(write[0].split('?')[1]).getAll('charge_item_ids')).toEqual(['-1']);
  });

  it('explica carnê vigente e bloqueio de renovação sem permitir selecionar ou gerar', async () => {
    const data = simulation(12);
    const reason = 'Carnê vigente até 11/2027. A renovação estará disponível no mês do último vencimento.';
    const blocked = { ...data, to_generate: 0, already_generated: 12,
      items: data.items.map(item => ({ ...item, already_generated: true, carne_renewal_blocked: true, carne_renewal_reason: reason })),
      carne_renewal_blocks: [{ payer_client_id: 115, payer_name: 'Cliente do carne', contract_ids: [668], reason }] };
    api.fetch.mockResolvedValueOnce(blocked);
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByText('Renovação indisponível')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Selecionar carnê de Cliente do carne' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nada a gerar' })).toBeDisabled();
  });

  it('abre a conferência de PDFs por cliente somente após o clique no resultado', async () => {
    const user = await setup();
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    await user.click(await screen.findByRole('button', { name: 'Confirmar e gerar: 12 título(s)' }));
    await screen.findByText('Lote de fechamento #30');
    expect(api.delivery.mock.lastCall?.[0]).toMatchObject({ open: false, initialLoteId: 30, carneDownloadOnly: true });
    expect(screen.getByText(/encaminhar manualmente pelo WhatsApp/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Conferir e baixar carnês' }));
    expect(api.delivery.mock.lastCall?.[0]).toMatchObject({ open: true, initialLoteId: 30, initialBanco: 'sicredi', carneDownloadOnly: true });
    await user.click(screen.getByRole('button', { name: 'Enviar por e-mail' }));
    expect(api.delivery.mock.lastCall?.[0]).toMatchObject({ open: true, initialLoteId: 30, initialBanco: 'sicredi', carneDownloadOnly: false });
    expect(api.fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });
});
