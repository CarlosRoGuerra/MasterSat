import type { ReactNode } from 'react';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ClientesPage from './page';
import type { BillingItem } from './_components/types';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'admin' }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', user: { role: auth.role }, error: '' }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('@/lib/assistant-context', () => ({ useAssistantContextActions: vi.fn() }));
vi.mock('next/navigation', () => {
  const params = new URLSearchParams();
  const router = { replace: vi.fn(), push: vi.fn() };
  return { useSearchParams: () => params, useRouter: () => router };
});

const client = { id: 7, name: 'Cliente de teste', cpf_cnpj: '00000000000', type: 'pf', status: 'ativo' };
const billing: BillingItem = { id: 38512, contract_id: 9, billing_type: 'recorrente', period_label: '10/2026',
  due_date: '2026-10-15', amount: 64.99, status: 'pendente', titulo_bancario: { estado: 'sem_titulo' } };
let wallet: BillingItem[];
let queryClient: QueryClient;

function conflict(code: string, message: string) {
  return Object.assign(new Error(message), { status: 409, detail: { code, message } });
}

beforeEach(() => {
  vi.resetAllMocks();
  auth.role = 'admin';
  wallet = [{ ...billing }];
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  api.list.mockResolvedValue([]);
  api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
    if (path.startsWith('/clients/summary')) return { total: 1, active: 1, delinquent: 0, company: 0 };
    if (path.startsWith('/clients?')) return { items: [client], total: 1 };
    if (path.startsWith('/billings?')) return wallet;
    if (path.startsWith('/boletos/carne?')) return [];
    if (path.endsWith('/cancel')) {
      const updated = { ...wallet[0], status: 'cancelada' };
      wallet = [updated];
      return updated;
    }
    if (options.method === 'DELETE') { wallet = []; return { reabertas: [] }; }
    if (options.method === 'PUT') {
      const updated = { ...wallet[0], ...JSON.parse(String(options.body)) };
      wallet = [updated];
      return updated;
    }
    throw new Error(`Requisição inesperada: ${path}`);
  });
});
afterEach(() => { cleanup(); queryClient.clear(); vi.restoreAllMocks(); });

function renderPage() {
  render(<QueryClientProvider client={queryClient}><ClientesPage /></QueryClientProvider>);
}
async function openEditor(select = false) {
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByTitle('Central financeira / boletos do cliente'));
  const edit = await screen.findByTitle('Alterar boleto');
  if (select) await user.click(within(screen.getByRole('dialog')).getAllByRole('checkbox')[0]);
  await user.click(edit);
  const dialog = within(screen.getByRole('dialog', { name: 'Alterar boleto #38512' }));
  return { user, dialog };
}
const writes = (method: string) => api.fetch.mock.calls.filter(([, options]) => options?.method === method);

describe('cancelar e excluir pelo ícone Alterar boleto', () => {
  it.each(['admin', 'financeiro'])('perfil %s cancela com justificativa e permite excluir na mesma janela', async (role) => {
    auth.role = role;
    const { user, dialog } = await openEditor(true);
    expect(dialog.getByRole('button', { name: 'Excluir boleto' })).toBeDisabled();
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), '  Dispensa negociada  ');
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(await dialog.findByRole('status')).toHaveTextContent('Boleto #38512 cancelado no sistema.');
    expect(writes('POST')).toHaveLength(1);
    expect(writes('POST')[0][0]).toBe('/billings/38512/cancel');
    expect(JSON.parse(writes('POST')[0][1].body)).toEqual({ reason: 'Dispensa negociada',
      liberar_competencia: false, confirmar_boleto_ailos: false, reverter_substituicao: false });
    expect(dialog.getByRole('button', { name: 'Excluir boleto' })).toBeEnabled();
    expect(dialog.getByRole('button', { name: 'Cancelar boleto' })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar janela' }));
    const list = within(screen.getByRole('dialog', { name: /Boletos do cliente/ }));
    expect(list.getByText('Cancelada')).toBeInTheDocument();
    expect(list.queryByText(/Selecionados:/)).not.toBeInTheDocument();
  });

  it('exige motivo e respeita a desistência antes de cancelar', async () => {
    const { user, dialog } = await openEditor();
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(dialog.getByRole('alert')).toHaveTextContent('Informe a justificativa do cancelamento.');
    expect(window.confirm).not.toHaveBeenCalled();
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    vi.mocked(window.confirm).mockReturnValue(false);
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(writes('POST')).toHaveLength(0);
    expect(dialog.getByRole('textbox', { name: /Justificativa/ })).toHaveValue('Negociação');
  });

  it.each([true, false])('exige a confirmação de boleto ativo no banco (aceita=%s)', async (accept) => {
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path.endsWith('/cancel')) {
        const body = JSON.parse(String(options.body));
        if (!body.confirmar_boleto_ailos) throw conflict('boleto_ailos_registrado', 'O boleto continua ativo no banco. Baixa manual necessária.');
        wallet = [{ ...billing, status: 'cancelada', titulo_bancario: { estado: 'registrado', baixa_status: 'pendente' } }];
        return wallet[0];
      }
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    vi.mocked(window.confirm).mockReturnValueOnce(true).mockReturnValueOnce(accept);
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Cancelamento solicitado');
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining('ativo no banco'));
    expect(writes('POST')).toHaveLength(accept ? 2 : 1);
    if (accept) {
      expect(JSON.parse(writes('POST')[1][1].body).confirmar_boleto_ailos).toBe(true);
      expect(dialog.getByRole('status')).toHaveTextContent('continua ativo no banco até a baixa');
      expect(dialog.getByRole('button', { name: 'Excluir boleto' })).toBeDisabled();
      expect(dialog.getByText(/A baixa bancária está pendente/)).toBeInTheDocument();
    } else {
      expect(dialog.queryByRole('status')).not.toBeInTheDocument();
      expect(wallet[0].status).toBe('pendente');
      expect(dialog.getByRole('button', { name: 'Cancelar boleto' })).toBeEnabled();
    }
  });

  it('mantém a janela e os dados quando a API recusa o cancelamento', async () => {
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path.endsWith('/cancel')) throw new Error('Registro bancário em andamento. Aguarde o desfecho.');
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Solicitação do cliente');
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('Registro bancário em andamento.');
    expect(dialog.getByRole('textbox', { name: /Justificativa/ })).toHaveValue('Solicitação do cliente');
    expect(dialog.queryByRole('status')).not.toBeInTheDocument();
  });

  it('exclui a cancelada, avisa que libera a competência e atualiza a carteira', async () => {
    wallet = [{ ...billing, status: 'cancelada' }];
    const { user, dialog } = await openEditor();
    await user.click(dialog.getByRole('button', { name: 'Excluir boleto' }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('competência 10/2026'));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('fechamento pode gerar a cobrança de novo'));
    expect(writes('DELETE')).toHaveLength(1);
    expect(writes('DELETE')[0][0]).toBe('/billings/38512');
    const list = within(await screen.findByRole('dialog', { name: /Boletos do cliente/ }));
    expect(list.getByRole('status')).toHaveTextContent('excluído da carteira; o histórico foi preservado');
    expect(list.queryByTitle('Alterar boleto')).not.toBeInTheDocument();
    await waitFor(() => expect(api.fetch.mock.calls.filter(([path]) => path.startsWith('/boletos/carne?'))).toHaveLength(2));
  });

  it('não exclui se a confirmação for recusada', async () => {
    wallet = [{ ...billing, status: 'cancelada' }];
    const { user, dialog } = await openEditor();
    vi.mocked(window.confirm).mockReturnValue(false);
    await user.click(dialog.getByRole('button', { name: 'Excluir boleto' }));
    expect(writes('DELETE')).toHaveLength(0);
    expect(screen.getByRole('dialog', { name: 'Alterar boleto #38512' })).toBeInTheDocument();
  });

  it('aguarda atualizar a carteira antes de fechar a exclusão', async () => {
    wallet = [{ ...billing, status: 'cancelada' }];
    let finish!: (value: BillingItem[]) => void;
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path.startsWith('/billings?') && wallet.length === 0) {
        return new Promise<BillingItem[]>((resolve) => { finish = resolve; });
      }
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    await user.click(dialog.getByRole('button', { name: 'Excluir boleto' }));
    expect(dialog.getByRole('button', { name: 'Excluindo…' })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar' }));
    expect(screen.getByRole('dialog', { name: 'Alterar boleto #38512' })).toBeInTheDocument();
    expect(writes('DELETE')).toHaveLength(1);
    finish([]);
    await screen.findByRole('dialog', { name: /Boletos do cliente/ });
  });

  it('só reabre originais de uma negociação após a confirmação exigida pela API', async () => {
    wallet = [{ ...billing, billing_type: 'boleto_unico', status: 'cancelada' }];
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (options.method === 'DELETE') {
        if (!path.includes('reverter_substituicao=true')) throw conflict('titulo_substituto', 'Esta cobrança substitui #1 e #2.');
        wallet = [{ ...billing, id: 1 }, { ...billing, id: 2 }];
        return { reabertas: [1, 2] };
      }
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    await user.click(dialog.getByRole('button', { name: 'Excluir boleto' }));
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining('excluir e reabrir as originais'));
    expect(writes('DELETE').map(([path]) => path)).toEqual(['/billings/38512', '/billings/38512?reverter_substituicao=true']);
    const list = within(await screen.findByRole('dialog', { name: /Boletos do cliente/ }));
    expect(list.getByRole('status')).toHaveTextContent('2 cobrança(s) original(is) reaberta(s).');
    await waitFor(() => expect(list.getAllByTitle('Alterar boleto')).toHaveLength(2));
  });

  it('preserva a cobrança na janela se a exclusão falhar', async () => {
    wallet = [{ ...billing, status: 'cancelada' }];
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (options.method === 'DELETE') throw new Error('Cobrança com histórico bancário não pode ser excluída.');
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    await user.click(dialog.getByRole('button', { name: 'Excluir boleto' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('histórico bancário');
    expect(wallet).toHaveLength(1);
    expect(dialog.getByRole('button', { name: 'Excluir boleto' })).toBeEnabled();
  });

  it.each(['registrado', 'baixado', 'em_registro', 'desfecho_desconhecido', 'remessa_cnab'] as const)(
    'bloqueia a exclusão de cancelada com título %s', async (estado) => {
      wallet = [{ ...billing, status: 'cancelada', titulo_bancario: { estado } }];
      const { dialog } = await openEditor();
      expect(dialog.getByRole('button', { name: 'Excluir boleto' })).toBeDisabled();
      expect(dialog.getByText(/histórico bancário e a conciliação/)).toBeInTheDocument();
    },
  );

  it('preserva o pagamento e impede todas as alterações de boleto pago', async () => {
    wallet = [{ ...billing, status: 'paga', paid_amount: 64.99 }];
    const { dialog } = await openEditor();
    expect(dialog.queryByRole('button', { name: 'Cancelar boleto' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Excluir boleto' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Salvar alteração' })).not.toBeInTheDocument();
    expect(dialog.getByRole('spinbutton', { name: /Valor/ })).toBeDisabled();
  });

  it('bloqueia fechamento e ações duplicadas durante o cancelamento', async () => {
    let finish!: (value: BillingItem) => void;
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => path.endsWith('/cancel')
      ? new Promise<BillingItem>((resolve) => { finish = resolve; }) : original(path, options));
    const { user, dialog } = await openEditor();
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(dialog.getByRole('button', { name: 'Cancelando…' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Fechar janela' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Salvar alteração' })).toBeDisabled();
    expect(dialog.getByRole('textbox', { name: /Justificativa/ })).toBeDisabled();
    await user.click(dialog.getByRole('button', { name: 'Fechar' }));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Alterar boleto #38512' })).toBeInTheDocument();
    wallet = [{ ...billing, status: 'cancelada' }];
    finish(wallet[0]);
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Cancelar boleto' })).toBeDisabled());
    expect(writes('POST')).toHaveLength(1);
  });

  it('não esvazia a carteira nem diz que a operação falhou quando só a atualização falha', async () => {
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation(async (path: string, options: RequestInit = {}) => {
      if (path.startsWith('/billings?') && wallet[0].status === 'cancelada') throw new Error('Rede indisponível');
      return original(path, options);
    });
    const { user, dialog } = await openEditor();
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Negociação');
    await user.click(dialog.getByRole('button', { name: 'Cancelar boleto' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('A operação foi concluída');
    expect(dialog.getByRole('status')).toHaveTextContent('cancelado no sistema');
    await user.click(dialog.getByRole('button', { name: 'Fechar janela' }));
    expect(within(screen.getByRole('dialog')).getByText('Cancelada')).toBeInTheDocument();
  });

  it('mantém a alteração de valor e vencimento funcionando', async () => {
    const { user, dialog } = await openEditor();
    await user.clear(dialog.getByRole('spinbutton', { name: /Valor/ }));
    await user.type(dialog.getByRole('spinbutton', { name: /Valor/ }), '70');
    await user.type(dialog.getByRole('textbox', { name: /Justificativa/ }), 'Ajuste negociado');
    await user.click(dialog.getByRole('button', { name: 'Salvar alteração' }));
    expect(JSON.parse(writes('PUT')[0][1].body)).toEqual({ amount: 70, justification: 'Ajuste negociado' });
    const list = within(await screen.findByRole('dialog', { name: /Boletos do cliente/ }));
    expect(list.getByRole('status')).toHaveTextContent('alterado com sucesso');
  });

  it('não oferece a central financeira ao operacional', async () => {
    auth.role = 'operacional';
    renderPage();
    await screen.findByText('Cliente de teste');
    expect(screen.queryByTitle('Central financeira / boletos do cliente')).not.toBeInTheDocument();
    expect(writes('DELETE')).toHaveLength(0);
  });
});
