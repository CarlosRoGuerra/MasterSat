import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VeiculosPage from './page';

const api = vi.hoisted(() => ({ fetch: vi.fn(), all: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchAll: api.all, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: 'admin' }, loading: false, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/components/vehicle-onboarding-wizard', () => ({ VehicleOnboardingWizard: () => null }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const params = new URLSearchParams();
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useSearchParams: () => params, useRouter: () => router };
});

const vehicle = { id: 743, plate: 'QIV3234', client_id: 356, status: 'ativo' };
const tracker = { id: 12, imei: '864421065724075', vehicle_id: 743, client_id: 356, status: 'instalado' };
let removed = false;
const choiceLabel = 'Deseja encerrar os contratos ativos deste veículo?';
const confirmationLabel = 'Tenho certeza de que desejo encerrar os contratos e interromper as mensalidades futuras.';
const uninstallCalls = () => api.fetch.mock.calls.filter(([path, options]) => path.includes('/uninstall?') && options?.method === 'POST');

beforeEach(() => {
  vi.resetAllMocks();
  removed = false;
  api.all.mockImplementation(async (path: string) => {
    if (path.startsWith('/vehicles')) return [{ ...vehicle, status: removed ? 'retirado' : 'ativo' }];
    if (path === '/clients') return [{ id: 356, name: 'Cliente do veículo' }];
    return [];
  });
  api.list.mockImplementation(async () => removed ? [] : [tracker]);
  api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
    if (path.includes('/uninstall?') && options.method === 'POST') {
      const params = new URL(path, 'http://local.test').searchParams;
      const close = params.get('encerrar_contratos') === 'true';
      removed = true;
      return { contracts_closed: close ? 1 : 0, contracts_kept_active: close ? 0 : 1 };
    }
    if (path === '/vehicles/743') return { ...vehicle, status: removed ? 'retirado' : 'ativo' };
    if (path.startsWith('/contracts')) return [{ id: 90, client_id: 356, status: 'ativo' }];
    return [];
  });
});
afterEach(cleanup);

async function openUninstall(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Ver rastreadores vinculados' }));
  await user.click(await screen.findByRole('button', { name: 'Desvincular' }));
  return within(screen.getByRole('dialog', { name: 'Registrar desinstalação' }));
}

describe('escolha dos contratos na desinstalação', () => {
  it('mantém contratos ativos por padrão e informa a continuidade das mensalidades', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = await openUninstall(user);
    expect(dialog.getByRole('checkbox', { name: choiceLabel })).not.toBeChecked();
    expect(dialog.queryByRole('checkbox', { name: confirmationLabel })).not.toBeInTheDocument();
    expect(dialog.getByText(/mensalidades continuarão normalmente/)).toBeInTheDocument();
    await user.click(dialog.getByRole('button', { name: 'Confirmar desinstalação' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Registrar desinstalação' })).not.toBeInTheDocument());
    expect(screen.getByText(/1 contrato\(s\) mantido\(s\) ativo\(s\)/)).toBeInTheDocument();
    const params = new URL(uninstallCalls()[0][0], 'http://local.test').searchParams;
    expect(params.get('encerrar_contratos')).toBe('false');
    expect(params.get('confirmar_encerramento_contratos')).toBe('false');
  });

  it('exige as duas opções antes de enviar o encerramento', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = await openUninstall(user);
    await user.click(dialog.getByRole('checkbox', { name: choiceLabel }));
    expect(dialog.getByRole('checkbox', { name: confirmationLabel })).not.toBeChecked();
    const confirm = dialog.getByRole('button', { name: 'Confirmar desinstalação' });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(uninstallCalls()).toHaveLength(0);
    await user.click(dialog.getByRole('checkbox', { name: confirmationLabel }));
    await user.click(confirm);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Registrar desinstalação' })).not.toBeInTheDocument());
    const params = new URL(uninstallCalls()[0][0], 'http://local.test').searchParams;
    expect(params.get('encerrar_contratos')).toBe('true');
    expect(params.get('confirmar_encerramento_contratos')).toBe('true');
    expect(screen.getByText(/1 contrato\(s\) encerrado\(s\)/)).toBeInTheDocument();
  });

  it('remove a segunda confirmação ao mudar a decisão ou a data', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = await openUninstall(user);
    const choice = dialog.getByRole('checkbox', { name: choiceLabel });
    await user.click(choice);
    await user.click(dialog.getByRole('checkbox', { name: confirmationLabel }));
    await user.click(choice);
    await user.click(choice);
    expect(dialog.getByRole('checkbox', { name: confirmationLabel })).not.toBeChecked();
    await user.click(dialog.getByRole('checkbox', { name: confirmationLabel }));
    fireEvent.change(dialog.getByLabelText('Data da desinstalação'), { target: { value: '2026-10-15' } });
    expect(dialog.getByRole('checkbox', { name: confirmationLabel })).not.toBeChecked();
    expect(dialog.getByRole('button', { name: 'Confirmar desinstalação' })).toBeDisabled();
  });

  it('reabre com as duas opções desmarcadas depois de desistir', async () => {
    const user = userEvent.setup();
    render(<VeiculosPage />);
    let dialog = await openUninstall(user);
    await user.click(dialog.getByRole('checkbox', { name: choiceLabel }));
    await user.click(dialog.getByRole('checkbox', { name: confirmationLabel }));
    await user.click(dialog.getByRole('button', { name: 'Cancelar' }));
    dialog = await openUninstall(user);
    expect(dialog.getByRole('checkbox', { name: choiceLabel })).not.toBeChecked();
    expect(dialog.queryByRole('checkbox', { name: confirmationLabel })).not.toBeInTheDocument();
    expect(uninstallCalls()).toHaveLength(0);
  });

  it('mantém a janela e a escolha quando a API recusa a operação', async () => {
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path, options) => {
      if (path.includes('/uninstall?')) throw new Error('Confira novamente os contratos.');
      return original(path, options);
    });
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = await openUninstall(user);
    await user.click(dialog.getByRole('checkbox', { name: choiceLabel }));
    await user.click(dialog.getByRole('checkbox', { name: confirmationLabel }));
    await user.click(dialog.getByRole('button', { name: 'Confirmar desinstalação' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('Confira novamente os contratos.');
    expect(dialog.getByRole('checkbox', { name: choiceLabel })).toBeChecked();
    expect(removed).toBe(false);
  });

  it('bloqueia novo envio, edição e fechamento durante a gravação', async () => {
    let finish!: (result: { contracts_closed: number; contracts_kept_active: number }) => void;
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path, options) => path.includes('/uninstall?')
      ? new Promise((resolve) => { finish = resolve; }) : original(path, options));
    const user = userEvent.setup();
    render(<VeiculosPage />);
    const dialog = await openUninstall(user);
    await user.click(dialog.getByRole('button', { name: 'Confirmar desinstalação' }));
    expect(dialog.getByRole('button', { name: 'Registrando…' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Cancelar' })).toBeDisabled();
    expect(dialog.getByRole('checkbox', { name: choiceLabel })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar' }));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Registrar desinstalação' })).toBeInTheDocument();
    finish({ contracts_closed: 0, contracts_kept_active: 1 });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Registrar desinstalação' })).not.toBeInTheDocument());
    expect(uninstallCalls()).toHaveLength(1);
  });
});
