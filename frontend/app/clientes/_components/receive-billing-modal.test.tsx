import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ReceiveBillingModal, type ReceiveBillingForm } from './receive-billing-modal';
import type { BillingItem } from './types';

const billing: BillingItem = {
  id: 42,
  billing_type: 'recorrente',
  due_date: '2026-09-15',
  amount: 120,
  status: 'pendente',
  period_label: '09/2026',
};

const form: ReceiveBillingForm = {
  paid_amount: '120',
  payment_date: '2026-09-14',
  payment_method: 'pix',
  notes: '',
};

function renderModal(over: { billing?: BillingItem | null; form?: ReceiveBillingForm; saving?: boolean } = {}) {
  const onSave = vi.fn();
  const onClose = vi.fn();
  const onFormChange = vi.fn();
  render(
    <ReceiveBillingModal
      billing={over.billing === undefined ? billing : over.billing}
      form={over.form ?? form}
      saving={over.saving ?? false}
      onFormChange={onFormChange}
      onClose={onClose}
      onSave={onSave}
    />,
  );
  return { onSave, onClose, onFormChange };
}

describe('ReceiveBillingModal', () => {
  it('não renderiza quando não há cobrança selecionada', () => {
    renderModal({ billing: null });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('identifica a cobrança e o mês de referência no título', () => {
    renderModal();
    expect(screen.getByText('Registrar pagamento — boleto #42')).toBeInTheDocument();
    expect(screen.getByText('Mês de referência: 09/2026')).toBeInTheDocument();
  });

  it('confirma o pagamento pelo botão de ação', async () => {
    const { onSave } = renderModal();
    await userEvent.click(screen.getByRole('button', { name: 'Confirmar pagamento' }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('bloqueia envio duplicado enquanto salva', () => {
    renderModal({ saving: true });
    expect(screen.getByRole('button', { name: 'Registrando…' })).toBeDisabled();
  });

  it('avisa que a baixa manual não cancela o título quando há boleto registrado na Ailos', () => {
    renderModal({ billing: { ...billing, boleto_ailos: true } });
    expect(screen.getByText(/não cancela o título no banco/i)).toBeInTheDocument();
  });

  it('não mostra o aviso da Ailos quando não há boleto registrado', () => {
    renderModal();
    expect(screen.queryByText(/não cancela o título no banco/i)).not.toBeInTheDocument();
  });

  it('mostra o valor com juros como referência quando a cobrança está vencida', () => {
    renderModal({ billing: { ...billing, status: 'vencida', valor_com_juros: 131.4 } });
    expect(screen.getByText(/Com juros até hoje: R\$\s?131,40/)).toBeInTheDocument();
  });
});
