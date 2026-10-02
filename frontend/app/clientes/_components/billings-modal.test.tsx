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
});
