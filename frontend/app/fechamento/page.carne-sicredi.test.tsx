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
vi.mock('@/app/clientes/_components/closure-delivery-modal', () => ({ ClosureDeliveryModal: () => null }));

function simulation(count: number, service = false) {
  const items = Array.from({ length: count }, (_, offset) => {
    const due = new Date(Date.UTC(2026, 11 + offset, 10)).toISOString().slice(0, 10);
    return { type: 'recorrente', contract_id: 668, client_id: 115, payer_client_id: 115,
      client_name: 'Cliente do carne', payer_name: 'Cliente do carne', client_type: 'pf',
      vehicle_plate: 'ABC1234', tracker_imei: null, plan_name: 'Mensalidade', plan_price: 64.99,
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
afterEach(cleanup);

async function setup() {
  const user = userEvent.setup();
  const { container } = render(<FechamentoPage />);
  fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-11' } });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Forma de cobrança' }), 'carne_sicredi');
  return user;
}

describe('fechamento do carne Sicredi', () => {
  it('previews twelve monthly rows and twelve separate titles with the default twelve installments', async () => {
    const user = await setup();
    expect(screen.getByRole('spinbutton', { name: 'Parcelas do carnê' })).toHaveValue(12);
    await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
    expect(await screen.findByText('Títulos previstos (12)')).toBeInTheDocument();
    expect(screen.getByText('Parcela 1/12')).toBeInTheDocument();
    expect(screen.getByText('Parcela 12/12')).toBeInTheDocument();
    expect(screen.getByText('10/12/2026')).toBeInTheDocument();
    expect(screen.getByText('10/11/2027')).toBeInTheDocument();
    expect(screen.getByText('12 de 12 selecionados')).toBeInTheDocument();
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
    expect(screen.getAllByText('· vencimento em 12/2026')).toHaveLength(1);
    const firstTitle = screen.getByText('· vencimento em 12/2026').closest('div')!;
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
    expect(screen.getByText(/carnê em andamento aparece como já gerado/)).toBeInTheDocument();
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
});
