import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { ClosureDeliveryModal } from './closure-delivery-modal';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

function item(id: number, estado = 'pronto', extra = {}) {
  return {
    billing_id: id,
    lote_id: 10,
    cliente: `Cliente ${id}`,
    email: `cliente${id}@example.test`,
    valor: 65,
    vencimento: '2026-11-15',
    documentos: ['Boleto'],
    emitir_nfse: 'nao',
    nfse_status: null,
    boleto_emitido: true,
    estado,
    motivo: null,
    ...extra,
  };
}
let rows: ReturnType<typeof item>[];
let sendFailure: number | null;
let previewFailure: boolean;
const calls = () =>
  vi.mocked(apiFetch).mock.calls.filter(([path]) => String(path).includes('/enviar/'));

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  sendFailure = null;
  previewFailure = false;
  rows = [
    item(1),
    item(2),
    item(3, 'bloqueado', {
      boleto_emitido: false,
      motivo: 'Boleto Ailos não emitido',
    }),
    item(4, 'enviado'),
    item(5, 'desconhecido'),
    item(6, 'bloqueado', { boleto_emitido: false, motivo: 'Cobrança não está em aberto' }),
  ];
  vi.mocked(apiFetch).mockImplementation(async (path) => {
    if (path === '/billing-closure/lotes/recuperar') return {};
    if (path === '/billing-closure/meses')
      return [
        { mes_servico: '2026-10', total_lotes: 1 },
        { mes_servico: '2026-09', total_lotes: 1 },
      ];
    if (String(path).includes('/meses/')) {
      if (previewFailure) throw new Error('Conferência indisponível');
      return {
        mes_servico: String(path).endsWith('2026-09') ? '2026-09' : '2026-10',
        total_lotes: 1,
        total: rows.length,
        prontos: rows.filter((r) => r.estado === 'pronto').length,
        enviados: rows.filter((r) => r.estado === 'enviado').length,
        bloqueados: rows.filter((r) => r.estado === 'bloqueado').length,
        indeterminados: 1,
        itens: rows,
      };
    }
    if (String(path).includes('/enviar/')) {
      const id = Number(String(path).split('/').pop());
      if (id === sendFailure) {
        rows = rows.map((r) => (r.billing_id === id ? { ...r, estado: 'desconhecido' } : r));
        throw new Error('Resultado incerto');
      }
      rows = rows.map((r) => (r.billing_id === id ? { ...r, estado: 'enviado' } : r));
      return { estado: 'enviado' };
    }
    throw new Error(`Unexpected request: ${path}`);
  });
});

async function abrir() {
  const user = userEvent.setup();
  const onClose = vi.fn();
  render(<ClosureDeliveryModal open token="test-token" onClose={onClose} />);
  await waitFor(() => expect(screen.getByLabelText('Mês do serviço')).toBeEnabled());
  await user.selectOptions(screen.getByLabelText('Mês do serviço'), '2026-10');
  await screen.findByRole('checkbox', { name: 'Selecionar cobrança #1' });
  return { user, onClose };
}

describe('ClosureDeliveryModal', () => {
  it('seleciona a quantidade informada no filtro e envia somente esses títulos', async () => {
    const { user } = await abrir();
    await user.click(screen.getByRole('button', { name: 'Prontas 2' }));
    await user.clear(screen.getByLabelText('Quantidade do lote'));
    await user.type(screen.getByLabelText('Quantidade do lote'), '1');
    await user.click(screen.getByRole('button', { name: 'Selecionar' }));
    expect(screen.getByRole('checkbox', { name: 'Selecionar cobrança #1' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Selecionar cobrança #2' })).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Enviar selecionadas (1)' }));
    await screen.findByText('1 envio(s) concluído(s).');
    expect(calls().map(([path]) => path)).toEqual(['/billing-closure/lotes/10/enviar/1']);
  });

  it('ignora pendentes, enviados e incertos no envio; permite sucesso parcial', async () => {
    sendFailure = 2;
    const { user } = await abrir();
    expect(screen.getByRole('checkbox', { name: 'Selecionar cobrança #4' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Selecionar cobrança #5' })).toBeDisabled();
    await user.click(
      screen.getByRole('checkbox', {
        name: 'Selecionar todas as cobranças disponíveis neste filtro',
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Enviar selecionadas (2)' }));
    await screen.findByText('1 envio(s) concluído(s); 1 não concluído(s).');
    expect(calls()).toHaveLength(2);
    expect(screen.getByRole('checkbox', { name: 'Selecionar cobrança #2' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Enviar selecionadas (0)' })).toBeDisabled();
  });

  it('limpa a seleção ao trocar o mês e rejeita quantidade inválida', async () => {
    const { user } = await abrir();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar cobrança #1' }));
    await user.clear(screen.getByLabelText('Quantidade do lote'));
    await user.type(screen.getByLabelText('Quantidade do lote'), '0');
    expect(screen.getByRole('button', { name: 'Selecionar' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Mês do serviço'), '2026-09');
    expect(
      await screen.findByRole('checkbox', { name: 'Selecionar cobrança #1' }),
    ).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Enviar selecionadas (0)' })).toBeDisabled();
  });

  it('invalida a prévia quando a atualização falha, impedindo envio com dados antigos', async () => {
    const { user } = await abrir();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar cobrança #1' }));
    previewFailure = true;
    await user.click(screen.getByRole('button', { name: 'Atualizar conferência' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: 'Enviar selecionadas (0)' })).toBeDisabled();
  });
});
