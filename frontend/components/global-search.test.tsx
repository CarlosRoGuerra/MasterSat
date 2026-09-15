import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { GlobalSearch } from './global-search';
import { apiFetch } from '@/lib/api';
import { useCurrentUser } from '@/lib/use-current-user';
import { AssistantContextProvider, useAssistantContextActions, type ContextualAction } from '@/lib/assistant-context';
import type { GlobalSearchOut } from '@/lib/domain-types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/auth', () => ({ getAccessToken: () => 'test-token' }));

const push = vi.fn();
let pathname = '/dashboard';
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => pathname,
}));

vi.mock('@/lib/use-current-user', () => ({ useCurrentUser: vi.fn() }));

const mockApiFetch = vi.mocked(apiFetch);
const mockUseCurrentUser = vi.mocked(useCurrentUser);

const EMPTY_RESULT: GlobalSearchOut = {
  clients: [], vehicles: [], trackers: [], service_orders: [], contracts: [], documents: [],
};

function resultWith(overrides: Partial<GlobalSearchOut>): GlobalSearchOut {
  return { ...EMPTY_RESULT, ...overrides };
}

function mockRole(role: 'admin' | 'operacional' | 'financeiro') {
  mockUseCurrentUser.mockReturnValue({
    data: { id: 1, name: 'Teste', email: 't@t.com', role },
  } as ReturnType<typeof useCurrentUser>);
}

/** Envolve com o Provider real (montado em produção via PageShell) — leve o bastante para não precisar de mock. */
function renderPalette(ui: React.ReactElement) {
  return render(<AssistantContextProvider>{ui}</AssistantContextProvider>);
}

/** Componente auxiliar só para publicar ações contextuais nos testes, como uma aba/página faria. */
function ContextualActionsProbe({ actions }: { actions: ContextualAction[] }) {
  useAssistantContextActions(actions);
  return null;
}

beforeEach(() => {
  mockApiFetch.mockReset();
  push.mockReset();
  pathname = '/dashboard';
  mockRole('admin');
});

describe('GlobalSearch', () => {
  it('abre a paleta ao clicar no gatilho e fecha com Esc', async () => {
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    expect(screen.getByRole('dialog', { name: 'Busca global' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();
  });

  it('Ctrl+K abre a paleta de qualquer lugar da página', async () => {
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog', { name: 'Busca global' })).toBeInTheDocument();
  });

  it('espera o usuário parar de digitar antes de buscar (debounce)', async () => {
    mockApiFetch.mockResolvedValue(EMPTY_RESULT);
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'jo');

    expect(mockApiFetch).not.toHaveBeenCalled();
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(mockApiFetch).toHaveBeenCalledWith('/search?q=jo', {}, 'test-token');
  });

  it('não busca com menos de 2 caracteres', async () => {
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

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
    renderPalette(<GlobalSearch />);

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
    renderPalette(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'xyz nao existe');

    expect(await screen.findByText(/Não encontramos resultados para/)).toBeInTheDocument();
  });

  it('mostra erro quando a API falha', async () => {
    mockApiFetch.mockRejectedValue(new Error('Falha de rede'));
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

    await user.click(screen.getByRole('button', { name: 'Busca global' }));
    await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'joao');

    expect(await screen.findByText('Falha de rede')).toBeInTheDocument();
  });

  it('Enter navega para o resultado ativo e fecha a paleta', async () => {
    mockApiFetch.mockResolvedValue(resultWith({
      clients: [{ id: 7, entity: 'client', title: 'Maria Souza', subtitle: '98765432100', status: 'ativo' }],
    }));
    const user = userEvent.setup();
    renderPalette(<GlobalSearch />);

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
    renderPalette(<GlobalSearch />);

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
    renderPalette(
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

  describe('Assistente de Ações', () => {
    it('paleta vazia mostra Ações (mais usadas) e Navegação', async () => {
      const user = userEvent.setup();
      renderPalette(<GlobalSearch />);

      await user.click(screen.getByRole('button', { name: 'Busca global' }));

      expect(screen.getByText('Ações')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Novo cliente/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Novo veículo/ })).toBeInTheDocument();

      expect(screen.getByText('Navegação')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Financeiro/ })).toBeInTheDocument();
    });

    it('encontra uma ação por texto digitado e navega ao selecioná-la', async () => {
      const user = userEvent.setup();
      renderPalette(<GlobalSearch />);

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'novo veic');

      const actionButton = await screen.findByRole('button', { name: /Novo veículo/ });
      await user.click(actionButton);

      expect(push).toHaveBeenCalledWith('/veiculos?assistantAction=new');
      expect(screen.queryByRole('dialog', { name: 'Busca global' })).not.toBeInTheDocument();
    });

    it('pré-preenche o cliente ao criar veículo a partir de /clientes?focus=42', async () => {
      pathname = '/clientes';
      // JSDOM permite reatribuir window.location.search diretamente.
      window.history.pushState({}, '', '/clientes?focus=42');

      const user = userEvent.setup();
      renderPalette(<GlobalSearch />);

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'novo veic');
      await user.click(await screen.findByRole('button', { name: /Novo veículo/ }));

      expect(push).toHaveBeenCalledWith('/veiculos?assistantAction=new&prefillClientId=42');
    });

    it('some com ações que a role do usuário não teria acesso', async () => {
      mockRole('financeiro');
      const user = userEvent.setup();
      renderPalette(<GlobalSearch />);

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'novo veic');

      // "Novo veículo" exige admin/operacional — financeiro não pode ver nem executar.
      await new Promise((r) => setTimeout(r, 50));
      expect(screen.queryByRole('button', { name: /Novo veículo/ })).not.toBeInTheDocument();
    });

    it('executa uma ação contextual sem confirmação e mostra feedback de sucesso', async () => {
      const run = vi.fn().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderPalette(
        <>
          <GlobalSearch />
          <ContextualActionsProbe actions={[{ id: 'os-iniciar', label: 'Iniciar OS', run }]} />
        </>,
      );

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.click(await screen.findByRole('button', { name: 'Iniciar OS' }));

      expect(run).toHaveBeenCalledTimes(1);
      expect(await screen.findByText(/Iniciar OS concluído/)).toBeInTheDocument();
    });

    it('ação contextual com confirm só executa depois de confirmar, e não executa ao cancelar', async () => {
      const run = vi.fn().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderPalette(
        <>
          <GlobalSearch />
          <ContextualActionsProbe
            actions={[{
              id: 'os-cancelar',
              label: 'Cancelar OS',
              run,
              confirm: { title: 'Cancelar OS #123?', description: 'Cancelar a OS #123 de João da Silva?', confirmLabel: 'Sim, cancelar', danger: true },
            }]}
          />
        </>,
      );

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.click(await screen.findByRole('button', { name: 'Cancelar OS' }));

      const dialog = await screen.findByText('Cancelar a OS #123 de João da Silva?');
      expect(dialog).toBeInTheDocument();
      expect(run).not.toHaveBeenCalled();

      await user.click(screen.getByRole('button', { name: 'Cancelar' }));
      expect(run).not.toHaveBeenCalled();
      expect(screen.queryByText('Cancelar a OS #123 de João da Silva?')).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Cancelar OS' }));
      await user.click(await screen.findByRole('button', { name: 'Sim, cancelar' }));
      expect(run).toHaveBeenCalledTimes(1);
    });

    it('mostra erro amigável com "Tentar novamente" quando a ação falha', async () => {
      const run = vi.fn().mockRejectedValueOnce(new Error('Falha ao concluir')).mockResolvedValueOnce(undefined);
      const user = userEvent.setup();
      renderPalette(
        <>
          <GlobalSearch />
          <ContextualActionsProbe actions={[{ id: 'registrar-pagamento', label: 'Registrar pagamento', run }]} />
        </>,
      );

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.click(await screen.findByRole('button', { name: 'Registrar pagamento' }));

      expect(await screen.findByText('Falha ao concluir')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Tentar novamente' }));

      expect(run).toHaveBeenCalledTimes(2);
      expect(await screen.findByText(/Registrar pagamento concluído/)).toBeInTheDocument();
    });

    it('trocar rastreador: escolhe o veículo e navega com o verbo da ação', async () => {
      mockApiFetch.mockResolvedValue(resultWith({
        vehicles: [{ id: 9, entity: 'vehicle', title: 'ABC1D23', subtitle: null, status: 'ativo', client_id: 1 }],
      }));
      const user = userEvent.setup();
      renderPalette(<GlobalSearch />);

      await user.click(screen.getByRole('button', { name: 'Busca global' }));
      await user.type(screen.getByPlaceholderText(/Buscar cliente/), 'trocar rastreador');
      await user.click(await screen.findByRole('button', { name: 'Trocar rastreador' }));

      expect(screen.getByPlaceholderText(/Selecione o veículo/)).toBeInTheDocument();

      await user.type(screen.getByPlaceholderText(/Selecione o veículo/), 'abc');
      await user.click(await screen.findByRole('button', { name: /ABC1D23/ }));

      expect(push).toHaveBeenCalledWith('/veiculos?focus=9&assistantAction=trocar-rastreador');
    });
  });
});
