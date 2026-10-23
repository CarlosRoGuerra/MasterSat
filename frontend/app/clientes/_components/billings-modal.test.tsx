import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { BillingsModal } from './billings-modal';
import type { BillingItem } from './types';

const parent: BillingItem = {
  id: 56870,
  billing_type: 'boleto_unico',
  title: 'Fechamento 09/2026 - boleto único',
  due_date: '2026-10-15',
  payment_date: '2026-10-02',
  amount: 129.98,
  paid_amount: 129.98,
  status: 'paga',
  period_label: '09/2026',
};

describe('BillingsModal', () => {
  const props = () => ({
    open: true, clientName: 'VIGFAT', loading: false, carnes: [], carneExpandido: null,
    summaryExpanded: false, selectedIds: [], gerandoCarne: false,
    onClose: vi.fn(), onToggleSummary: vi.fn(), onSelectedIdsChange: vi.fn(),
    onToggleCarne: vi.fn(), onBaixarCarne: vi.fn(), onOpenUnify: vi.fn(),
    onGerarCarne: vi.fn(), onEditBilling: vi.fn(), onBillingHistory: vi.fn(),
    onReceiveBilling: vi.fn(), onReceiveSgr: vi.fn(), onSendEmail: vi.fn(), onSendWhats: vi.fn(),
    onBaixarPdf: vi.fn(), onBaixarComprovante: vi.fn(), onLoadComponents: vi.fn(),
  });
  const sgr = (id: number, status = 'vencida', period = '08/2026'): BillingItem => ({
    id, billing_type: 'recorrente', amount: 64.99, due_date: '2026-09-25', status,
    sgr_payload: { cod_boleto: '19398' }, period_label: period,
  });

  it('oferece baixa no boleto SGR fechado e envia todos os itens em aberto, inclusive fora do filtro', async () => {
    const handlers = props();
    const items = [sgr(1), sgr(2), sgr(3, 'pendente', '07/2026'), sgr(4, 'paga'), sgr(5, 'cancelada')];
    render(<BillingsModal {...handlers} billings={items} />);
    await userEvent.selectOptions(screen.getByRole('combobox'), 'vencida');
    await userEvent.click(screen.getByRole('button', { name: 'Dar baixa' }));
    expect(handlers.onReceiveSgr).toHaveBeenCalledExactlyOnceWith('19398', items.slice(0, 3));
    expect(screen.queryByTitle('Alterar boleto')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Ver itens' }));
    expect(screen.getAllByTitle('Marcar como pago (baixa manual)')).toHaveLength(2);
  });

  it('não oferece baixa para boleto SGR sem itens em aberto', () => {
    render(<BillingsModal {...props()} billings={[sgr(1, 'paga'), sgr(2, 'cancelada')]} />);
    expect(screen.queryByRole('button', { name: 'Dar baixa' })).not.toBeInTheDocument();
  });

  it('exibe boleto único como grupo e mostra seus itens sem duplicar o total', async () => {
    const onLoadComponents = vi.fn().mockResolvedValue([
      { id: 11, billing_type: 'recorrente', title: 'Mensalidade', vehicle_plate: 'ABC1234', amount: 64.99, due_date: '2026-10-15', status: 'cancelada', substituted_by_id: 56870 },
      { id: 12, billing_type: 'recorrente', title: 'Mensalidade', vehicle_plate: 'DEF5678', amount: 64.99, due_date: '2026-10-15', status: 'cancelada', substituted_by_id: 56870 },
    ] satisfies BillingItem[]);
    render(<BillingsModal
      open clientName="AD TRANSPORTES" loading={false} billings={[parent]} carnes={[]}
      carneExpandido={null} summaryExpanded selectedIds={[]} gerandoCarne={false}
      onClose={vi.fn()} onToggleSummary={vi.fn()} onSelectedIdsChange={vi.fn()}
      onToggleCarne={vi.fn()} onBaixarCarne={vi.fn()} onOpenUnify={vi.fn()}
      onGerarCarne={vi.fn()} onEditBilling={vi.fn()} onBillingHistory={vi.fn()}
      onReceiveBilling={vi.fn()} onSendEmail={vi.fn()} onSendWhats={vi.fn()}
      onBaixarPdf={vi.fn()} onBaixarComprovante={vi.fn()}
      onLoadComponents={onLoadComponents}
    />);

    expect(screen.getByText('Boleto MasterSat')).toBeInTheDocument();
    expect(screen.queryByText('Avulsa')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Ver itens do boleto 56870' }));
    expect(await screen.findByText('ABC1234')).toBeInTheDocument();
    expect(screen.getByText('DEF5678')).toBeInTheDocument();
    expect(screen.getByText('2 itens · 2 placa(s)')).toBeInTheDocument();
    expect(onLoadComponents).toHaveBeenCalledWith(56870);
    expect(screen.getByText('Mostrando 1 boleto(s)')).toBeInTheDocument();
  });

  it('carnê simples aparece marcado e não pode virar carnê na Ailos', () => {
    const parcela = (id: number, somente_sistema: boolean): BillingItem => ({
      id, billing_type: 'carne', title: `Plano 64,99 • parcela ${id}/3`, due_date: '2099-01-15',
      amount: 64.99, status: 'pendente', installment_number: id, installment_total: 3, somente_sistema,
    });
    const props = {
      open: true, clientName: 'CLIENTE', loading: false, carnes: [], carneExpandido: null,
      summaryExpanded: false, gerandoCarne: false,
      onClose: vi.fn(), onToggleSummary: vi.fn(), onSelectedIdsChange: vi.fn(),
      onToggleCarne: vi.fn(), onBaixarCarne: vi.fn(), onOpenUnify: vi.fn(),
      onGerarCarne: vi.fn(), onEditBilling: vi.fn(), onBillingHistory: vi.fn(),
      onReceiveBilling: vi.fn(), onSendEmail: vi.fn(), onSendWhats: vi.fn(),
      onBaixarPdf: vi.fn(), onBaixarComprovante: vi.fn(), onLoadComponents: vi.fn(),
    };
    const { rerender } = render(<BillingsModal {...props} billings={[parcela(1, true), parcela(2, true)]} selectedIds={[1, 2]} />);
    expect(screen.getAllByText('Carnê simples · só no sistema')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Gerar carnê' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Unificar em 1 boleto' })).toBeEnabled();

    rerender(<BillingsModal {...props} billings={[parcela(1, false), parcela(2, false)]} selectedIds={[1, 2]} />);
    expect(screen.queryByText('Carnê simples · só no sistema')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gerar carnê' })).toBeEnabled();
  });
});
