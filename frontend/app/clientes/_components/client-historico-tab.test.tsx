import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ClientHistoricoTab } from './client-historico-tab';
import { apiFetch, type Page } from '@/lib/api';
import type { TimelineEvent } from '@/lib/domain-types';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, apiFetch: vi.fn() };
});

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

const mockApiFetch = vi.mocked(apiFetch);

function event(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: 'client:1:created',
    category: 'cliente',
    type: 'client_created',
    occurred_at: new Date().toISOString(),
    title: 'Cliente cadastrado',
    description: 'João Silva foi cadastrado no sistema.',
    severity: 'info',
    actor_name: null,
    link: null,
    metadata: null,
    ...overrides,
  };
}

function page(items: TimelineEvent[], total = items.length): Page<TimelineEvent> {
  return { items, total };
}

const DEFAULT_PROPS = {
  clientId: 1,
  token: 'test-token',
  canViewFinance: true,
  isAdmin: true,
  onExportPdf: vi.fn(),
  onOpenBillings: vi.fn(),
};

beforeEach(() => {
  mockApiFetch.mockReset();
  push.mockReset();
  DEFAULT_PROPS.onExportPdf.mockReset();
  DEFAULT_PROPS.onOpenBillings.mockReset();
});

describe('ClientHistoricoTab', () => {
  it('busca e renderiza os eventos ao montar', async () => {
    mockApiFetch.mockResolvedValue(page([event()]));
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    expect(await screen.findByText('Cliente cadastrado')).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith('/clients/1/timeline?skip=0&limit=20', {}, 'test-token');
  });

  it('mostra o skeleton de carregamento antes da resposta chegar', async () => {
    let resolve: (v: Page<TimelineEvent>) => void;
    mockApiFetch.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { container } = render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
    resolve!(page([event()]));
    await waitFor(() => expect(container.querySelector('.animate-pulse')).not.toBeInTheDocument());
  });

  it('mostra estado vazio quando não há eventos', async () => {
    mockApiFetch.mockResolvedValue(page([]));
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);
    expect(await screen.findByText('Nenhum evento registrado.')).toBeInTheDocument();
  });

  it('mostra erro quando a busca falha', async () => {
    mockApiFetch.mockRejectedValue(new Error('Falha de rede'));
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);
    expect(await screen.findByText('Falha de rede')).toBeInTheDocument();
  });

  it('agrupa eventos por data (Hoje / Meses anteriores)', async () => {
    mockApiFetch.mockResolvedValue(page([
      event({ id: 'a', title: 'Evento de hoje', occurred_at: new Date().toISOString() }),
      event({ id: 'b', title: 'Evento antigo', occurred_at: new Date('2020-01-01T10:00:00Z').toISOString() }),
    ]));
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    expect(await screen.findByText('Evento de hoje')).toBeInTheDocument();
    expect(screen.getByText('Evento antigo')).toBeInTheDocument();
    expect(screen.getByText('Hoje')).toBeInTheDocument();
    expect(screen.getByText('Meses anteriores')).toBeInTheDocument();
  });

  it('troca de filtro refaz a busca com a categoria escolhida', async () => {
    mockApiFetch.mockResolvedValue(page([event()]));
    const user = userEvent.setup();
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    await screen.findByText('Cliente cadastrado');
    mockApiFetch.mockResolvedValue(page([event({ id: 'v1', category: 'veiculo', title: 'Veículo adicionado' })]));
    await user.click(screen.getByRole('button', { name: 'Veículos' }));

    expect(await screen.findByText('Veículo adicionado')).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenLastCalledWith('/clients/1/timeline?skip=0&limit=20&category=veiculo', {}, 'test-token');
  });

  it('esconde os chips financeiro/contratos e auditoria por permissão', async () => {
    mockApiFetch.mockResolvedValue(page([event()]));
    render(<ClientHistoricoTab {...DEFAULT_PROPS} canViewFinance={false} isAdmin={false} />);

    await screen.findByText('Cliente cadastrado');
    expect(screen.queryByRole('button', { name: 'Financeiro' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Contratos' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Auditoria' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Veículos' })).toBeInTheDocument();
  });

  it('"Carregar mais" busca a próxima página e concatena os eventos', async () => {
    mockApiFetch.mockResolvedValueOnce(page([event({ id: 'e1', title: 'Primeiro evento' })], 2));
    const user = userEvent.setup();
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    await screen.findByText('Primeiro evento');
    const loadMore = screen.getByRole('button', { name: /Carregar mais/ });

    mockApiFetch.mockResolvedValueOnce(page([event({ id: 'e2', title: 'Segundo evento' })], 2));
    await user.click(loadMore);

    expect(await screen.findByText('Segundo evento')).toBeInTheDocument();
    expect(screen.getByText('Primeiro evento')).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenLastCalledWith('/clients/1/timeline?skip=1&limit=20', {}, 'test-token');
  });

  it('expande um evento e navega ao clicar em "Ver registro"', async () => {
    mockApiFetch.mockResolvedValue(page([
      event({ id: 'veh1', category: 'veiculo', title: 'Veículo adicionado', link: { entity: 'vehicle', id: 5 } }),
    ]));
    const user = userEvent.setup();
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    await user.click(await screen.findByText('Veículo adicionado'));
    const verRegistro = await screen.findByRole('button', { name: /Ver registro/ });
    await user.click(verRegistro);

    expect(push).toHaveBeenCalledWith('/veiculos?focus=5');
  });

  it('evento financeiro chama onOpenBillings em vez de navegar', async () => {
    mockApiFetch.mockResolvedValue(page([
      event({ id: 'bill1', category: 'financeiro', title: 'Cobrança gerada' }),
    ]));
    const user = userEvent.setup();
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    await user.click(await screen.findByText('Cobrança gerada'));
    const verCobrancas = await screen.findByRole('button', { name: /Ver cobranças/ });
    await user.click(verCobrancas);

    expect(DEFAULT_PROPS.onOpenBillings).toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it('chama onExportPdf ao clicar em Exportar PDF', async () => {
    mockApiFetch.mockResolvedValue(page([event()]));
    const user = userEvent.setup();
    render(<ClientHistoricoTab {...DEFAULT_PROPS} />);

    await screen.findByText('Cliente cadastrado');
    await user.click(screen.getByRole('button', { name: /Exportar PDF/ }));
    expect(DEFAULT_PROPS.onExportPdf).toHaveBeenCalled();
  });
});
