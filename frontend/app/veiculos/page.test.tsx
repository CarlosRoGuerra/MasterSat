import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import VeiculosPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), all: vi.fn(), list: vi.fn(), role: 'admin' }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchAll: api.all, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: api.role }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/components/vehicle-onboarding-wizard', () => ({ VehicleOnboardingWizard: () => null }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const params = new URLSearchParams();
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useSearchParams: () => params, useRouter: () => router };
});

const initial = { id: 7, client_id: 1, plate: 'ISCARF16017741', is_non_road_asset: true, status: 'ativo', type: 'Máquina' };
let vehicles = [{ ...initial }];
beforeEach(() => {
  api.role = 'admin';
  vi.clearAllMocks();
  vehicles = [{ ...initial }];
  api.all.mockImplementation(async (path: string) => {
    if (path.startsWith('/vehicles')) return vehicles;
    if (path === '/clients') return [{ id: 1, name: 'Cliente ativo', cpf_cnpj: '12345678909' }];
    return [];
  });
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/vehicles/lote/excluir') {
      const body = JSON.parse(String(options?.body));
      if (!body.simular) vehicles = [];
      return { simulacao: body.simular, total_enviados: 1, aplicados: 1, ignorados: 0,
        itens: [{ vehicle_id: 7, plate: initial.plate, situacao: 'aplicado', motivo: null }] };
    }
    if (options?.method === 'PUT') return { ...initial, ...JSON.parse(String(options.body)) };
    return [];
  });
});

describe('Veículos: equipamentos sem placa e seleção', () => {
  it('editar mantém o identificador completo e a classificação', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    await user.click(await screen.findByRole('button', { name: 'Editar veículo' }));
    const editor = within(screen.getByRole('dialog', { name: 'Editar veículo' }));
    expect(editor.getByPlaceholderText('Identificador do equipamento')).toHaveValue(initial.plate);
    expect(editor.getByRole('checkbox', { name: /Máquina ou equipamento/ })).toBeChecked();
    await user.click(editor.getByRole('button', { name: 'Atualizar veículo' }));
    await waitFor(() => expect(api.fetch).toHaveBeenCalledWith('/vehicles/7', expect.objectContaining({ method: 'PUT' }), 'test-token'));
    const [, options] = api.fetch.mock.calls.find(([, opts]) => opts?.method === 'PUT')!;
    expect(JSON.parse(options.body)).toMatchObject({ plate: initial.plate, is_non_road_asset: true });
  });

  it('desmarcar equipamento impede truncar um identificador longo', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    await user.click(await screen.findByRole('button', { name: 'Editar veículo' }));
    const editor = within(screen.getByRole('dialog', { name: 'Editar veículo' }));
    await user.click(editor.getByRole('checkbox', { name: /Máquina ou equipamento/ }));
    await user.click(editor.getByRole('button', { name: 'Atualizar veículo' }));
    expect(await editor.findByText(/preservar o identificador completo/)).toBeInTheDocument();
    expect(api.fetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
    expect(editor.getByPlaceholderText('Placa')).toHaveValue(initial.plate);
  });

  it('selecionar todos usa a seleção para conferir e excluir, depois atualiza a lista', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    await screen.findByText(initial.plate);
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar todos os veículos do filtro' }));
    expect(screen.getByRole('checkbox', { name: `Selecionar ${initial.plate}` })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Excluir selecionados' }));
    await user.click(await screen.findByRole('button', { name: 'Excluir 1 veículo(s)' }));
    await screen.findByText('Nenhum veículo encontrado');
    const calls = api.fetch.mock.calls.filter(([path]) => path === '/vehicles/lote/excluir');
    expect(calls.map(([, options]) => JSON.parse(options.body))).toEqual([
      { ids: [7], simular: true }, { ids: [7], simular: false },
    ]);
    expect(screen.queryByText('1 selecionado(s)')).not.toBeInTheDocument();
  });

  it('operacional não vê seleção para excluir', async () => {
    api.role = 'operacional';
    render(<VeiculosPage />);
    await screen.findByText(initial.plate);
    expect(screen.queryByRole('checkbox', { name: /Selecionar/ })).not.toBeInTheDocument();
  });
});
