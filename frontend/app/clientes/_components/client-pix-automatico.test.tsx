import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { ClientPixAutomatico } from './client-pix-automatico';
import type { Client } from './types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const client: Client = { id: 10, name: 'Cliente de teste', cpf_cnpj: '12345678901', type: 'pf', status: 'ativo',
  forma_cobranca: 'pix_automatico', billing_day: 20 };
const empty = { conexao: { configurado: true, escrita_habilitada: false, ambiente: 'producao' },
  recorrencia: null, cobrancas: [], pendentes_adesao: 0 };

beforeEach(() => vi.mocked(apiFetch).mockReset());

describe('Pix Automático no cadastro', () => {
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
