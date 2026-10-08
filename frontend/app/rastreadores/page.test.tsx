import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RastreadoresPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), all: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({
  ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchAll: api.all, apiFetchList: api.list,
}));
vi.mock('@/lib/use-auth-guard', () => ({
  useAuthGuard: () => ({ token: 'local-token', user: { role: 'admin' }, loading: false, error: '' }),
}));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const params = new URLSearchParams();
  const router = { replace: vi.fn() };
  return { useSearchParams: () => params, useRouter: () => router };
});

const clients = [
  { id: 1, name: 'Cliente anterior', cpf_cnpj: '11111111111' },
  { id: 2, name: 'Carlos Roberto', cpf_cnpj: '15964802702' },
];
const vehicles = [
  { id: 7, client_id: 1, plate: 'OXD0A94', model: 'Modelo atual' },
  { id: 8, client_id: 2, plate: 'ABC1D23', model: 'Outro veículo' },
];
const initialTracker = {
  id: 5, imei: 'DES000005', brand: 'suntec', model: 'ST 300', status: 'instalado',
  client_id: 1, vehicle_id: 7, client_name: 'Cliente anterior', vehicle_plate: 'OXD0A94',
  install_date: '2025-11-14', active_plan_id: 10, active_plan_name: 'Plano atual',
};
let tracker = { ...initialTracker };
let saveError = '';

beforeEach(() => {
  tracker = { ...initialTracker };
  saveError = '';
  vi.clearAllMocks();
  api.all.mockImplementation(async (path: string) => {
    if (path.startsWith('/trackers')) return [tracker];
    if (path === '/clients') return clients;
    if (path === '/vehicles') return vehicles;
    throw new Error(`Requisição inesperada: ${path}`);
  });
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options?: RequestInit) => {
    if (options?.method === 'POST' || options?.method === 'PUT') {
      if (saveError) throw new Error(saveError);
      const body = JSON.parse(String(options.body));
      tracker = { ...tracker, ...(body.tracker_update || body), client_id: body.client_id ?? tracker.client_id };
      return tracker;
    }
    if (path.startsWith('/plans')) return [{ id: 10, name: 'Plano atual', price: 64.99 }];
    if (path === '/integrations/multiportal/manufacturers') return [];
    if (path === '/integrations/multiportal/status') return { enabled: false };
    if (path.endsWith('/history')) return [];
    if (path.startsWith('/contracts?')) return [{ id: 20, tracker_id: 5, vehicle_id: 7, plan_id: 10, status: 'ativo' }];
    if (path.startsWith('/trackers/billing-day-suggestion')) return { dia: 10, origem: 'contrato_ativo', contrato_id: 20, alternativas: [] };
    throw new Error(`Requisição inesperada: ${path}`);
  });
});

function mutations() {
  return api.fetch.mock.calls.filter(([, options]) => ['POST', 'PUT', 'DELETE'].includes(options?.method));
}

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  render(<RastreadoresPage />);
  const buttons = await screen.findAllByRole('button', { name: 'Editar' });
  await user.click(buttons[0]);
  return within(screen.getByRole('dialog', { name: 'Editar rastreador' }));
}

async function selectByDocument(user: ReturnType<typeof userEvent.setup>, editor: Awaited<ReturnType<typeof openEditor>>) {
  await user.type(editor.getByPlaceholderText('CPF/CNPJ do cliente'), '15964802702');
  await user.click(editor.getByRole('button', { name: 'Buscar cliente' }));
}

describe('Troca de cliente pela edição do rastreador', () => {
  it.each(['CPF/CNPJ', 'nome'])('mantém a placa ao buscar por %s e salva a troca junto com os dados técnicos', async (search) => {
    const user = userEvent.setup();
    const editor = await openEditor(user);
    if (search === 'CPF/CNPJ') {
      await selectByDocument(user, editor);
    } else {
      await user.click(editor.getByRole('button', { name: 'Remover seleção' }));
      expect(editor.getByRole('combobox', { name: 'Veículo vinculado' })).toHaveValue('7');
      await user.type(editor.getByPlaceholderText('Buscar cliente por nome…'), 'Carlos');
      await user.click(editor.getByRole('button', { name: /Carlos Roberto/ }));
    }
    expect(editor.getByRole('combobox', { name: 'Veículo vinculado' })).toHaveValue('7');
    expect(editor.getByRole('option', { name: /OXD0A94/ })).toBeInTheDocument();
    expect(editor.getByText(/Contrato ativo neste veículo/)).toBeInTheDocument();
    expect(editor.getByText(/O novo cliente será o pagador/)).toBeInTheDocument();
    await user.clear(editor.getByPlaceholderText('Modelo'));
    await user.type(editor.getByPlaceholderText('Modelo'), 'ST 300 atualizado');
    await user.click(editor.getByRole('button', { name: 'Atualizar rastreador' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Editar rastreador' })).not.toBeInTheDocument());
    const calls = mutations();
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe('/trackers/5/change-client');
    const body = JSON.parse(calls[0][1].body);
    expect(body).toMatchObject({ client_id: 2, expected_vehicle_id: 7, expected_client_id: 1, tracker_update: { model: 'ST 300 atualizado', install_date: '2025-11-14', status: 'instalado' } });
    expect(body.tracker_update).not.toHaveProperty('imei');
    expect(body.tracker_update).not.toHaveProperty('client_id');
    expect(body.tracker_update).not.toHaveProperty('vehicle_id');
  });

  it('mantém a seleção e a placa se a API recusar uma tela desatualizada', async () => {
    const user = userEvent.setup();
    const editor = await openEditor(user);
    await selectByDocument(user, editor);
    saveError = 'O cliente do veículo mudou. Atualize a tela antes de trocar o cliente.';
    await user.click(editor.getByRole('button', { name: 'Atualizar rastreador' }));
    expect(await editor.findByText(saveError)).toBeInTheDocument();
    expect(editor.getByText('Carlos Roberto')).toBeInTheDocument();
    expect(editor.getByRole('combobox', { name: 'Veículo vinculado' })).toHaveValue('7');
    expect(mutations()).toHaveLength(1);
  });

  it('continua exigindo desinstalação quando o operador remove a placa', async () => {
    const user = userEvent.setup();
    const editor = await openEditor(user);
    await user.selectOptions(editor.getByRole('combobox', { name: 'Veículo vinculado' }), '');
    await user.click(editor.getByRole('button', { name: 'Atualizar rastreador' }));
    expect(await editor.findByText('Use a desinstalação do veículo para remover um vínculo existente.')).toBeInTheDocument();
    expect(mutations()).toHaveLength(0);
  });

  it('preserva o identificador importado também na edição técnica comum', async () => {
    const user = userEvent.setup();
    const editor = await openEditor(user);
    await user.type(editor.getByPlaceholderText('Observações técnicas'), 'Observação');
    await user.click(editor.getByRole('button', { name: 'Atualizar rastreador' }));
    await waitFor(() => expect(mutations()).toHaveLength(1));
    const [path, options] = mutations()[0];
    expect(path).toBe('/trackers/5');
    expect(options.method).toBe('PUT');
    expect(JSON.parse(options.body)).not.toHaveProperty('imei');
    expect(tracker.imei).toBe('DES000005');
  });
});
