import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import FechamentoPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({
  useAuthGuard: () => ({ token: 'test-token', user: { role: 'admin' }, loading: false, error: '' }),
}));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));

let corrected = false;
const charge = { type: 'servico', item_id: 51, client_id: 7, payer_client_id: 7,
  client_name: 'Cliente de teste', client_type: 'pf', vehicle_plate: 'ABC1D23',
  title: 'Teste', quantity: 1, unit_price: 5, installment_count: 1, generated_count: 0,
  remaining_installments: 1, per_installment_amount: 5, amount_to_generate: 5,
  total_remaining: 5, start_date: '2026-11-10' };

beforeEach(() => {
  vi.resetAllMocks();
  corrected = false;
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
    if (path === '/client-charge-items/51' && options.method === 'PUT') {
      corrected = true;
      return { ...charge, date_basis: 'due_date' };
    }
    if (path.startsWith('/billing-closure/simulate?')) {
      const october = path.includes('service_month=2026-10');
      const items = october && !corrected ? [] : [{ ...charge, date_basis: corrected ? 'due_date' : 'service_date' }];
      return { reference_month: october ? '10/2026' : '11/2026', billing_month: october ? '11/2026' : '12/2026',
        total_contracts: 0, to_generate: 0, already_generated: 0, total_amount: 0,
        items: [], uninstall_events: [], total_uninstall_fees: 0, charge_items: items,
        total_services: items.length * 5, grand_total: items.length * 5, payer_boleto_formats: { 7: 'unico' } };
    }
    throw new Error(`Requisição inesperada: ${path}`);
  });
});

it('corrige a interpretação da data sem alterá-la e encontra o adicional no fechamento de outubro', async () => {
  const user = userEvent.setup();
  const { container } = render(<FechamentoPage />);
  fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-11' } });
  await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
  await user.click(await screen.findByRole('button', { name: 'Editar Teste' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Editar lançamento' }));
  await user.selectOptions(dialog.getByRole('combobox', { name: 'Usar a data como' }), 'due_date');
  expect(dialog.getByLabelText('Vencimento')).toHaveValue('2026-11-10');
  await user.click(dialog.getByRole('button', { name: 'Salvar alterações' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const writes = api.fetch.mock.calls.filter(([, options]) => options?.method === 'PUT');
  expect(writes).toHaveLength(1);
  expect(JSON.parse(writes[0][1].body)).toMatchObject({ start_date: '2026-11-10', date_basis: 'due_date' });
  await user.click(screen.getByRole('button', { name: 'Alterar escopo' }));
  fireEvent.change(container.querySelector('input[type="month"]')!, { target: { value: '2026-10' } });
  await user.click(screen.getByRole('button', { name: 'Simular fechamento' }));
  expect(await screen.findByText('vencimento 10/11/2026')).toBeInTheDocument();
  expect(screen.getByText('Serviços de 10/2026 · vencimentos em 11/2026')).toBeInTheDocument();
});
