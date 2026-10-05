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
    if (path === '/billing-closure/lotes')
      return [
        { id: 10, mes_servico: '2026-10', criado_em: '2026-10-01', total_titulos: rows.length },
        { id: 11, mes_servico: '2026-10', criado_em: '2026-10-02', total_titulos: 1 },
        { id: 12, mes_servico: '2026-09', criado_em: '2026-09-01', total_titulos: 1 },
      ];
    if (/^\/billing-closure\/lotes\/\d+$/.test(String(path))) {
      if (previewFailure) throw new Error('Conferência indisponível');
      const id = Number(String(path).split('/').pop());
      const batchRows = rows.filter((r) => r.lote_id === id);
      return {
        id,
        mes_servico: String(path).endsWith('2026-09') ? '2026-09' : '2026-10',
        total_lotes: 1,
        total: batchRows.length,
        prontos: batchRows.filter((r) => r.estado === 'pronto').length,
        enviados: batchRows.filter((r) => r.estado === 'enviado').length,
        bloqueados: batchRows.filter((r) => r.estado === 'bloqueado').length,
        indeterminados: 1,
        itens: batchRows,
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
  await user.selectOptions(screen.getByLabelText('Número do lote'), '10');
  await screen.findByRole('checkbox', { name: 'Selecionar cobrança #1' });
  return { user, onClose };
}

describe('ClosureDeliveryModal', () => {
  it('seleciona a quantidade informada no filtro e envia somente esses títulos', async () => {
    const { user } = await abrir();
    await user.click(screen.getByRole('button', { name: 'Prontas 2' }));
    await user.clear(screen.getByLabelText('Seleção parcial (opcional)'));
    await user.type(screen.getByLabelText('Seleção parcial (opcional)'), '1');
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

  it('limpa a seleção ao trocar o lote e rejeita quantidade inválida', async () => {
    const { user } = await abrir();
    await user.click(screen.getByRole('checkbox', { name: 'Selecionar cobrança #1' }));
    await user.clear(screen.getByLabelText('Seleção parcial (opcional)'));
    await user.type(screen.getByLabelText('Seleção parcial (opcional)'), '0');
    expect(screen.getByRole('button', { name: 'Selecionar' })).toBeDisabled();
    rows.push(item(7, 'pronto', { lote_id: 11 }));
    await user.selectOptions(screen.getByLabelText('Número do lote'), '11');
    expect(await screen.findByRole('checkbox', { name: 'Selecionar cobrança #7' })).not.toBeChecked();
    expect(screen.queryByRole('checkbox', { name: 'Selecionar cobrança #1' })).not.toBeInTheDocument();
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


it('envia mais de 25 títulos do lote completo, mesmo com filtro e seleção parcial', async () => {
  rows = [
    ...Array.from({ length: 61 }, (_, i) => item(i + 1)),
    item(70, 'bloqueado'), item(71, 'enviado'), item(72, 'desconhecido'),
    item(80, 'pronto', { lote_id: 11 }),
  ];
  const { user } = await abrir();
  await user.type(screen.getByLabelText('Seleção parcial (opcional)'), '1');
  await user.click(screen.getByRole('button', { name: 'Selecionar' }));
  await user.click(screen.getByRole('button', { name: 'Pendentes 1' }));
  await user.click(screen.getByRole('button', { name: 'Enviar lote completo (61)' }));
  await screen.findByText('61 envio(s) concluído(s).');
  expect(calls().map(([path]) => path)).toEqual(
    Array.from({ length: 61 }, (_, i) => `/billing-closure/lotes/10/enviar/${i + 1}`),
  );
});

it('abre diretamente o lote recém-gerado e mostra todos os destinatários', async () => {
  rows[0].email = 'principal@example.test, adicional@example.test';
  render(<ClosureDeliveryModal open token="test-token" initialLoteId={10} onClose={vi.fn()} />);
  await screen.findByText('principal@example.test, adicional@example.test');
  expect(screen.getByLabelText('Número do lote')).toHaveValue('10');
  expect(screen.getByRole('button', { name: 'Enviar lote completo (2)' })).toBeEnabled();
});
