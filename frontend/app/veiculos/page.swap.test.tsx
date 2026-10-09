import type { ReactNode } from 'react';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VeiculosPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), all: vi.fn(), list: vi.fn() }));
const route = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchAll: api.all, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'admin' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/components/vehicle-onboarding-wizard', () => ({ VehicleOnboardingWizard: () => null }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useSearchParams: () => route.params, useRouter: () => router };
});

const vehicle = { id: 743, plate: 'QIV3234', client_id: 356, status: 'ativo', brand: 'RENAULT', model: 'SANDERO' };
const oldTracker = { id: 12, imei: '864421065724075', vehicle_id: 743, client_id: 356, status: 'instalado', brand: 'Suntech', model: 'ST 300' };
const newTracker = { id: 700, imei: '866557080296066', vehicle_id: null, client_id: null, status: 'em_estoque', brand: 'J16', model: 'J16' };
const result = { old_tracker: { ...oldTracker, vehicle_id: null, client_id: null, status: 'em_estoque' },
  new_tracker: { ...newTracker, vehicle_id: 743, client_id: 356, status: 'instalado' }, contracts_updated: [669], contracts_reconciled: [] };
const conflict = Object.assign(new Error('O rastreador atual também consta em contratos de outras placas.'), {
  status: 409,
  detail: { code: 'inconsistent_active_contract_assignment', stale_contracts: [{ id: 223, vehicle_id: 293, vehicle_plate: 'TPV6I75' }] },
});

beforeEach(() => {
  vi.resetAllMocks();
  route.params.delete('focus');
  route.params.delete('assistantAction');
  api.all.mockImplementation(async (path: string) => {
    if (path.startsWith('/vehicles')) return [vehicle];
    if (path.startsWith('/trackers')) return [newTracker];
    if (path === '/clients') return [{ id: 356, name: 'Cliente do Sandero' }];
    return [];
  });
  // Deliberately stale: a pending list request must not replace the
  // committed swap response with the old equipment.
  api.list.mockResolvedValue([oldTracker]);
  api.fetch.mockImplementation(async (path: string) => path.endsWith('/swap') ? result : []);
});
afterEach(cleanup);

async function openSwap() {
  const user = userEvent.setup();
  render(<VeiculosPage />);
  await user.click(await screen.findByRole('button', { name: 'Ver rastreadores vinculados' }));
  await user.click(await screen.findByRole('button', { name: 'Trocar rastreador' }));
  const dialog = within(screen.getByRole('dialog', { name: 'Trocar rastreador' }));
  const search = dialog.getByPlaceholderText('Buscar por IMEI, marca ou modelo…');
  await user.click(search);
  await user.type(search, '866557');
  await user.click(await dialog.findByRole('button', { name: /866557080296066/ }));
  await user.type(dialog.getByRole('textbox', { name: 'Motivo da troca' }), 'posição');
  return { user, dialog };
}

describe('troca de rastreador pela tela do veículo', () => {
  it('mostra o novo IMEI ao concluir e envia a placa esperada', async () => {
    const { user, dialog } = await openSwap();
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    const trackerView = within(await screen.findByRole('dialog', { name: 'Rastreadores — QIV3234' }));
    expect(trackerView.getByText(newTracker.imei)).toBeInTheDocument();
    expect(trackerView.queryByText(oldTracker.imei)).not.toBeInTheDocument();
    const [, options] = api.fetch.mock.calls.find(([path]) => path.endsWith('/swap'))!;
    expect(JSON.parse(options.body)).toEqual({ new_tracker_id: 700, reason: 'posição', expected_vehicle_id: 743 });
    expect(api.all).toHaveBeenCalledWith('/trackers?available_for_swap=true', 'test-token');
  });

  it('mantém a janela aberta e mostra o erro da API dentro dela', async () => {
    api.fetch.mockImplementation(async (path: string) => {
      if (path.endsWith('/swap')) throw new Error('O novo rastreador não está mais em estoque.');
      return [];
    });
    const { user, dialog } = await openSwap();
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('O novo rastreador não está mais em estoque.');
    expect(screen.getByRole('dialog', { name: 'Trocar rastreador' })).toBeInTheDocument();
    expect(screen.queryByText(/substituído por/)).not.toBeInTheDocument();
  });

  it('preserva o veículo aberto pelo assistente mesmo fora da lista filtrada', async () => {
    route.params.set('focus', '743');
    route.params.set('assistantAction', 'trocar-rastreador');
    api.all.mockImplementation(async (path: string) => path.startsWith('/trackers') ? [newTracker] : []);
    api.fetch.mockImplementation(async (path: string) => path === '/vehicles/743' ? vehicle : path.endsWith('/swap') ? result : []);
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = within(await screen.findByRole('dialog', { name: 'Trocar rastreador' }));
    await user.click(dialog.getByPlaceholderText('Buscar por IMEI, marca ou modelo…'));
    await user.type(dialog.getByPlaceholderText('Buscar por IMEI, marca ou modelo…'), '866557');
    await user.click(await dialog.findByRole('button', { name: /866557080296066/ }));
    await user.type(dialog.getByRole('textbox', { name: 'Motivo da troca' }), 'posição');
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    const details = within(await screen.findByRole('dialog', { name: 'QIV3234' }));
    expect(details.getByText(newTracker.imei)).toBeInTheDocument();
    expect(details.queryByText(oldTracker.imei)).not.toBeInTheDocument();
  });

  it('exige conferir o contrato de outra placa antes de liberar a referência', async () => {
    let attempts = 0;
    api.fetch.mockImplementation(async (path: string) => {
      if (!path.endsWith('/swap')) return [];
      if (++attempts === 1) throw conflict;
      return { ...result, contracts_reconciled: [223] };
    });
    const { user, dialog } = await openSwap();
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    const check = await dialog.findByRole('checkbox', { name: /contrato #223 \(TPV6I75\)/ });
    expect(check).not.toBeChecked();
    expect(dialog.getByRole('button', { name: 'Confirmar troca' })).toBeDisabled();
    expect(dialog.getByText(/Os contratos continuam ativos/)).toBeInTheDocument();
    await user.click(check);
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    await screen.findByRole('dialog', { name: 'Rastreadores — QIV3234' });
    const calls = api.fetch.mock.calls.filter(([path]) => path.endsWith('/swap'));
    expect(JSON.parse(calls[0][1].body)).not.toHaveProperty('release_stale_contract_ids');
    expect(JSON.parse(calls[1][1].body).release_stale_contract_ids).toEqual([223]);
  });

  it('não fecha nem anuncia sucesso quando a resposta mantém os vínculos antigos', async () => {
    api.fetch.mockImplementation(async (path: string) => path.endsWith('/swap')
      ? { ...result, old_tracker: oldTracker, new_tracker: newTracker } : []);
    const { user, dialog } = await openSwap();
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('O servidor não confirmou os vínculos da troca.');
    expect(screen.queryByText(/substituído por/)).not.toBeInTheDocument();
  });

  it('impede fechar ou reenviar enquanto a troca está em andamento', async () => {
    let finish!: (value: typeof result) => void;
    api.fetch.mockImplementation(async (path: string) => path.endsWith('/swap')
      ? new Promise<typeof result>((resolve) => { finish = resolve; }) : []);
    const { user, dialog } = await openSwap();
    await user.click(dialog.getByRole('button', { name: 'Confirmar troca' }));
    expect(dialog.getByRole('button', { name: 'Trocando…' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Cancelar' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Remover seleção' })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar' }));
    expect(screen.getByRole('dialog', { name: 'Trocar rastreador' })).toBeInTheDocument();
    finish(result);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Trocar rastreador' })).not.toBeInTheDocument());
    expect(api.fetch.mock.calls.filter(([path]) => path.endsWith('/swap'))).toHaveLength(1);
  });
});
