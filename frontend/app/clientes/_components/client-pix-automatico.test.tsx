import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { ClientPixAutomatico } from './client-pix-automatico';
import type { Client } from './types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const client: Client = { id: 10, name: 'Cliente de teste', cpf_cnpj: '12345678901', type: 'pf', status: 'ativo',
  forma_cobranca: 'pix_automatico', billing_day: 20 };
const empty = { conexao: { configurado: true, escrita_habilitada: false, ambiente: 'producao' },
  recorrencia: null, cobrancas: [], pendentes_adesao: 0 };

beforeEach(() => vi.mocked(apiFetch).mockReset());
afterEach(() => vi.useRealTimers());

describe('Pix Automático no cadastro', () => {
  it('usa o vencimento da cobrança selecionada quando o Financeiro não informa billing_day', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-05T15:00:00Z'));
    vi.mocked(apiFetch).mockResolvedValue({ ...empty, conexao: { ...empty.conexao, escrita_habilitada: true } });
    render(<ClientPixAutomatico client={{ id: 10, name: 'Cliente de teste' }} token="token" vencimentoCobranca="2026-12-15" />);
    expect(await screen.findByLabelText('Início da autorização')).toHaveValue('2026-12-15');
    expect(screen.getByText(/Vencimento da cobrança selecionada/)).toHaveTextContent('15/12/2026');
    fireEvent.click(screen.getByRole('button', { name: 'Gerar autorização do cliente' }));
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true));
    const post = vi.mocked(apiFetch).mock.calls.find(([, options]) => options?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body)).data_inicial).toBe('2026-12-15');
  });

  it('usa a sugestão do servidor baseada na cobrança real ao abrir pelo cadastro', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...empty, primeiro_vencimento_sugerido: '2099-12-15' });
    render(<ClientPixAutomatico client={{ ...client, billing_day: 10 }} token="token" />);
    await waitFor(() => expect(screen.getByLabelText('Início da autorização')).toHaveValue('2099-12-15'));
    expect(vi.mocked(apiFetch).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  it('explica a autorização do dia 7 e a cobrança de dezembro sem afirmar que houve débito', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...empty, recorrencia: {
      id: 1, status: 'APROVADA', ativa: true, data_inicial: '2026-11-07',
    }, cobrancas: [{ id: 1, billing_id: 39537, status: 'NA_FILA', vencimento: '2026-12-15', valor: '74.99',
      enviada_em: null, inicio_janela_envio: '2026-12-05', fim_janela_envio: '2026-12-13' }] });
    render(<ClientPixAutomatico client={client} token="token" />);
    expect(await screen.findByText(/Autorização iniciada em/)).toHaveTextContent('07/11/2026');
    expect(screen.getByText(/Débito previsto para/)).toHaveTextContent('15/12/2026');
    expect(screen.getByText(/Janela de envio ao Sicredi/)).toHaveTextContent('05/12/2026 a 13/12/2026');
    expect(screen.getByText(/Esta cobrança ainda não foi enviada ao banco/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Consultar pagamento' })).not.toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('não habilita criação com início no passado', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...empty, conexao: { ...empty.conexao, escrita_habilitada: true },
      data_minima_autorizacao: '2099-12-15', primeiro_vencimento_sugerido: '2099-12-15' });
    render(<ClientPixAutomatico client={client} token="token" />);
    const field = await screen.findByLabelText('Início da autorização');
    await waitFor(() => expect(field).toHaveValue('2099-12-15'));
    fireEvent.change(field, { target: { value: '2026-11-07' } });
    expect(screen.getByRole('button', { name: 'Gerar autorização do cliente' })).toBeDisabled();
    expect(vi.mocked(apiFetch).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  it('abre apenas com consulta e não permite emissão em produção bloqueada', async () => {
    vi.mocked(apiFetch).mockResolvedValue(empty);
    render(<ClientPixAutomatico client={client} token="token" />);
    await screen.findByText('Autorizações e novos agendamentos ainda não estão liberados neste ambiente.');
    expect(screen.getByRole('button', { name: 'Gerar autorização do cliente' })).toBeDisabled();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith('/sicredi-pix/clientes/10', {}, 'token');
  });

  it('solicita autorização somente após a ação e mostra QR retornado pelo banco', async () => {
    let created = false;
    vi.mocked(apiFetch).mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') created = true;
      return { ...empty, conexao: { ...empty.conexao, escrita_habilitada: true }, recorrencia: created ? {
        id: 1, status: 'CRIADA', ativa: true, pix_copia_cola: 'QR-OFICIAL', qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
      } : null };
    });
    render(<ClientPixAutomatico client={client} token="token" />);
    const button = await screen.findByRole('button', { name: 'Gerar autorização do cliente' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByAltText('QR Code para autorizar Pix Automático');
    expect(screen.getByLabelText('Código de autorização')).toHaveValue('QR-OFICIAL');
    expect(screen.getByText('Aguardando autorização no banco')).toBeInTheDocument();
    const post = vi.mocked(apiFetch).mock.calls.find(([, options]) => options?.method === 'POST');
    expect(post?.[0]).toBe('/sicredi-pix/clientes/10/autorizacao');
    expect(JSON.parse(String(post?.[1]?.body))).toHaveProperty('data_inicial');
    expect(screen.queryByText('Autorizada pelo cliente')).not.toBeInTheDocument();
  });

  it('mostra erro de criação sem reenviar automaticamente', async () => {
    vi.mocked(apiFetch).mockImplementation(async (_path, options) => {
      if (options?.method === 'POST') throw new Error('Resultado desconhecido; consulte antes de reenviar.');
      return { ...empty, conexao: { ...empty.conexao, escrita_habilitada: true } };
    });
    render(<ClientPixAutomatico client={client} token="token" />);
    const button = await screen.findByRole('button', { name: 'Gerar autorização do cliente' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(await screen.findByRole('alert')).toHaveTextContent('Resultado desconhecido');
    expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });
});

describe('convite de autorização', () => {
  const criada = { ...empty, conexao: { ...empty.conexao, escrita_habilitada: true }, smtp_configurado: true,
    emails_responsavel: ['cliente@test.local'], recorrencia: { id: 1, status: 'CRIADA', ativa: true,
      data_inicial: '2099-12-20', pix_copia_cola: 'QR-OFICIAL', qr_svg: null, convite_status: 'enviado',
      convite_destino: 'cliente@test.local', convite_tentativas: 1, convite_enviado_em: '2026-11-16T12:00:00Z' } };

  it('reenvia pelo endpoint do convite, sem nova autorização no banco', async () => {
    vi.mocked(apiFetch).mockResolvedValue(criada);
    render(<ClientPixAutomatico client={client} token="token" />);
    expect(await screen.findByText(/Enviado por e-mail/)).toHaveTextContent('cliente@test.local');
    fireEvent.click(screen.getByRole('button', { name: 'Reenviar convite por e-mail' }));
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([, o]) => o?.method === 'POST')).toBe(true));
    const posts = vi.mocked(apiFetch).mock.calls.filter(([, o]) => o?.method === 'POST');
    expect(posts.map(([p]) => p)).toEqual(['/sicredi-pix/clientes/10/convite']);
  });

  it('sem e-mail do sistema o envio fica indisponível e o motivo aparece', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...criada, smtp_configurado: false, recorrencia: { ...criada.recorrencia,
      convite_status: 'erro', convite_erro: 'SMTP não configurado para envio por e-mail.' } });
    render(<ClientPixAutomatico client={client} token="token" />);
    expect(await screen.findByText('SMTP não configurado para envio por e-mail.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Enviar convite por e-mail' })).toBeDisabled();
    expect(screen.queryByRole('link', { name: /WhatsApp/ })).not.toBeInTheDocument();  // cliente sem telefone
  });

  it('QR ainda não devolvido: explica que o sistema consulta sozinho', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...criada, recorrencia: { ...criada.recorrencia, pix_copia_cola: null,
      convite_status: 'pendente' } });
    render(<ClientPixAutomatico client={client} token="token" />);
    expect(await screen.findByText(/ainda não devolveu o QR Code/)).toHaveTextContent('envia o convite por e-mail');
  });
});
