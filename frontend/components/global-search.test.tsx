import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { GlobalSearch } from './global-search';
import { apiFetch } from '@/lib/api';
import type { GlobalSearchOut } from '@/lib/domain-types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/auth', () => ({ getAccessToken: () => 'test-token' }));

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

const mockApiFetch = vi.mocked(apiFetch);

const EMPTY_RESULT: GlobalSearchOut = {
  clients: [], vehicles: [], trackers: [], service_orders: [], contracts: [], documents: [],
};

function resultWith(overrides: Partial<GlobalSearchOut>): GlobalSearchOut {
  return { ...EMPTY_RESULT, ...overrides };
}

beforeEach(() => {
  mockApiFetch.mockReset();
  push.mockReset();
});

describe('GlobalSearch', () => {
  it('abre a paleta ao clicar no gatilho e fecha com Esc', async () => {
    const user = userEvent.setup();
    render(<GlobalSearch />);

    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    expect(screen.getByRole('dialog', { name: 'Busca global' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();
  });

  it('Ctrl+K abre a paleta de qualquer lugar da página', async () => {
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog', { name: 'Busca global' })).toBeInTheDocument();
  });

  it('espera o usuário parar de digitar antes de buscar (debounce)', async () => {
    mockApiFetch.mockResolvedValue(EMPTY_RESULT);
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'jo');

    expect(mockApiFetch).not.toHaveBeenCalled();
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(mockApiFetch).toHaveBeenCalledWith('/search?q=jo', {}, 'test-token');
  });

  it('não busca com menos de 2 caracteres', async () => {
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'j');

    await new Promise((r) => setTimeout(r, 500));
    expect(mockApiFetch).not.toHaveBeenCalled();
    expect(screen.getByText(/Digite ao menos 2 caracteres/)).toBeInTheDocument();
  });

  it('agrupa os resultados por categoria', async () => {
    mockApiFetch.mockResolvedValue(resultWith({
      clients: [{ id: 1, entity: 'client', title: 'João da Silva', subtitle: '12345678901', status: 'ativo' }],
      vehicles: [{ id: 2, entity: 'vehicle', title: 'ABC1D23', subtitle: 'Toyota Corolla — João da Silva', status: 'ativo', client_id: 1 }],
    }));
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'joao');

    expect(await screen.findByText('Clientes')).toBeInTheDocument();
    expect(screen.getByText('Veículos')).toBeInTheDocument();
    // "joao" (sem til) não bate com o "João" do título via indexOf simples
    // (highlight() no cliente não faz unaccent — isso é só server-side), então
    // não quebra em <mark>: dá pra buscar o texto exato do título.
    expect(screen.getByText('João da Silva')).toBeInTheDocument();
    expect(screen.getByText('ABC1D23')).toBeInTheDocument();
  });

  it('mostra estado vazio quando não há resultados', async () => {
    mockApiFetch.mockResolvedValue(EMPTY_RESULT);
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'xyz nao existe');

    expect(await screen.findByText(/Não encontramos resultados para/)).toBeInTheDocument();
  });

  it('mostra erro quando a API falha', async () => {
    mockApiFetch.mockRejectedValue(new Error('Falha de rede'));
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'joao');

    expect(await screen.findByText('Falha de rede')).toBeInTheDocument();
  });

  it('Enter navega para o resultado ativo e fecha a paleta', async () => {
    mockApiFetch.mockResolvedValue(resultWith({
      clients: [{ id: 7, entity: 'client', title: 'Maria Souza', subtitle: '98765432100', status: 'ativo' }],
    }));
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'maria');
    // highlight() quebra "Maria" em <mark> dentro do título — busca pelo
    // texto acessível do botão inteiro, mesmo padrão de client-autocomplete.test.tsx.
    await screen.findByRole('button', { name: /Maria Souza/ });

    await user.keyboard('{Enter}');
    expect(push).toHaveBeenCalledWith('/clientes?focus=7');
    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();
  });

  it('navega entre categorias com as setas do teclado', async () => {
    mockApiFetch.mockResolvedValue(resultWith({
      clients: [{ id: 1, entity: 'client', title: 'João da Silva', subtitle: null, status: 'ativo' }],
      vehicles: [{ id: 2, entity: 'vehicle', title: 'ABC1D23', subtitle: null, status: 'ativo', client_id: 1 }],
    }));
    const user = userEvent.setup();
    render(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'jo');
    await screen.findByText('ABC1D23');

    // activeIndex começa no primeiro resultado (cliente) — uma seta desce
    // pro veículo, que é pra onde o Enter deve navegar.
    await user.keyboard('{ArrowDown}{Enter}');
    expect(push).toHaveBeenCalledWith('/veiculos?focus=2');
  });

  it('clicar fora fecha a paleta', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <div data-testid="outside">fora</div>
        <GlobalSearch />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    expect(screen.getByRole('dialog', { name: 'Busca global' })).toBeInTheDocument();

    await user.click(screen.getByTestId('outside'));
    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();
  });
});
