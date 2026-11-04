import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import NotasFiscaisPage from './page';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(), API_URL: 'http://api.test/api/v1' }));
vi.mock('@/lib/use-auth-guard', () => ({ useAuthGuard: () => ({ token: 'test-token', loading: false }) }));
vi.mock('@/components/page-shell', () => ({ PageShell: ({ children, title }: { children: ReactNode; title: string }) => <main><h1>{title}</h1>{children}</main> }));

const resumo = { competencia: '11/2026', autorizadas: 0, negadas: 0, processando: 0,
  total: 0, total_geral: 0, desconhecidas: 0 };
const loteFiscal = { id: 501, period_label: '10/2026', status: 'processando',
  total_notas: 1, total_autorizadas: 0, total_erro: 0, itens: [] };
const lote8 = { id: 8, mes_servico: '2026-10', criado_em: '2026-11-04', total_titulos: 1 };
let retardar: boolean;
let confirmarLote: boolean;
let resolver: (value: ReturnType<typeof previa>) => void;

function previa(batch: number | null) {
  return { period_label: '10/2026', closure_batch_id: confirmarLote ? batch : null,
    total_elegiveis: 1, ja_emitidas: 0, sem_configuracao: [], itens: [{
      billing_id: batch === 9 ? 102 : 101, client_id: 1, tomador: `Cliente lote ${batch ?? 'todos'}`,
      cpf_cnpj: '12345678901', valor: 64.99, titulo: 'Mensalidade', reprocessamento: false,
    }] };
}

beforeEach(() => {
  vi.clearAllMocks();
  retardar = false;
  confirmarLote = true;
  window.history.replaceState({}, '', '/notas-fiscais');
  vi.mocked(apiFetch).mockImplementation(async (path, options) => {
    if (path === '/nfse/resumo') return resumo;
    if (path === '/nfse/certificado') return null;
    if (path === '/billing-closure/lotes') return [lote8, { ...lote8, id: 9 }, { ...lote8, id: 10, mes_servico: '2026-11' }];
    if (String(path).startsWith('/nfse/lotes/elegiveis?')) {
      const params = new URLSearchParams(String(path).split('?')[1]);
      const batch = params.has('closure_batch_id') ? Number(params.get('closure_batch_id')) : null;
      if (retardar) return await new Promise(resolve => { resolver = resolve; });
      return previa(batch);
    }
    if (path === '/nfse/lotes' && options?.method === 'POST') return loteFiscal;
    if (path === '/nfse/lotes') return [];
    if (path === '/nfse/lotes/501') return loteFiscal;
    throw new Error(`Unexpected request: ${path}`);
  });
});

async function abrir() {
  const user = userEvent.setup();
  render(<NotasFiscaisPage />);
  await user.click(screen.getByRole('button', { name: 'Gerar lote' }));
  fireEvent.change(screen.getByLabelText(/Fechamento \(mês do serviço\)/), { target: { value: '2026-10' } });
  await waitFor(() => expect(screen.getByLabelText('Número do lote do fechamento')).toBeEnabled());
  return user;
}

const emissoes = () => vi.mocked(apiFetch).mock.calls.filter(([path, options]) => path === '/nfse/lotes' && options?.method === 'POST');

it('mostra apenas lotes do mês e consulta o número escolhido sem emitir notas', async () => {
  const user = await abrir();
  expect(screen.getByRole('option', { name: /Lote #8/ })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: /Lote #9/ })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /Lote #10/ })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '8');
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await screen.findByText('Cliente lote 8');
  const request = vi.mocked(apiFetch).mock.calls.find(([path]) => String(path).startsWith('/nfse/lotes/elegiveis?'))!;
  expect(new URLSearchParams(String(request[0]).split('?')[1]).get('closure_batch_id')).toBe('8');
  expect(screen.getByText(/Listagem de boletos.*Fechamento #8/)).toBeInTheDocument();
  expect(emissoes()).toHaveLength(0);
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '9');
  expect(screen.queryByText('Cliente lote 8')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Emitir' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await screen.findByText('Cliente lote 9');
});

it('abre o atalho do fechamento com mês e lote e conserva o número na confirmação', async () => {
  window.history.replaceState({}, '', '/notas-fiscais?aba=gerar&service_month=2026-10&closure_batch_id=8');
  const user = userEvent.setup();
  render(<NotasFiscaisPage />);
  await waitFor(() => expect(screen.getByLabelText('Número do lote do fechamento')).toHaveValue('8'));
  expect(emissoes()).toHaveLength(0);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Buscar boletos' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await screen.findByText('Cliente lote 8');
  await user.click(screen.getByRole('button', { name: 'Emitir' }));
  await screen.findByText(/Lote de NFS-e #501/);
  expect(JSON.parse(String(emissoes()[0][1]?.body))).toMatchObject({
    closure_batch_id: 8, period_label: '10/2026', billing_ids: [101],
  });
});

it('descarta uma consulta antiga se o usuário trocar o lote enquanto carrega', async () => {
  retardar = true;
  const user = await abrir();
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '8');
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '9');
  await act(async () => { resolver(previa(8)); });
  expect(screen.queryByText('Cliente lote 8')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Emitir' })).not.toBeInTheDocument();
});

it('limpa o lote e a seleção quando o mês muda', async () => {
  const user = await abrir();
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '8');
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await screen.findByText('Cliente lote 8');
  fireEvent.change(screen.getByLabelText(/Fechamento \(mês do serviço\)/), { target: { value: '2026-11' } });
  expect(screen.getByLabelText('Número do lote do fechamento')).toHaveValue('');
  expect(screen.queryByText('Cliente lote 8')).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: /Lote #10/ })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /Lote #8/ })).not.toBeInTheDocument();
});

it('impede confirmar quando a API não reconhece o lote selecionado', async () => {
  confirmarLote = false;
  const user = await abrir();
  await user.selectOptions(screen.getByLabelText('Número do lote do fechamento'), '8');
  await user.click(screen.getByRole('button', { name: 'Buscar boletos' }));
  await screen.findByText(/servidor não confirmou o lote/);
  expect(screen.queryByRole('button', { name: 'Emitir' })).not.toBeInTheDocument();
  expect(emissoes()).toHaveLength(0);
});
