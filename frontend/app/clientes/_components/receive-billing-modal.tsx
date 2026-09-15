import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { formatCurrency, valorComJuros } from './helpers';
import type { BillingItem } from './types';

export type ReceiveBillingForm = {
  paid_amount: string;
  payment_date: string;
  payment_method: string;
  notes: string;
};

export function ReceiveBillingModal({
  billing,
  form,
  saving,
  onFormChange,
  onClose,
  onSave,
}: {
  billing: BillingItem | null;
  form: ReceiveBillingForm;
  saving: boolean;
  onFormChange: (updater: (prev: ReceiveBillingForm) => ReceiveBillingForm) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const juros = billing ? valorComJuros(billing) : null;
  const temJuros = !!billing && juros != null && juros > billing.amount;

  return (
    <Modal
      open={!!billing}
      onClose={onClose}
      title={billing ? `Registrar pagamento — boleto #${billing.id}` : 'Registrar pagamento'}
      subtitle={billing?.period_label ? `Mês de referência: ${billing.period_label}` : 'Baixa manual da cobrança'}
      size="md"
    >
      <div className="space-y-4">
        <FormGrid>
          <FormField
            label="Valor pago (R$)"
            required
            hint={temJuros ? `Com juros até hoje: ${formatCurrency(juros!)}` : undefined}
          >
            <Input
              type="number"
              step="0.01"
              min="0.01"
              value={form.paid_amount}
              onChange={(e) => onFormChange((p) => ({ ...p, paid_amount: e.target.value }))}
            />
          </FormField>
          <FormField label="Data do pagamento" required>
            <Input
              type="date"
              value={form.payment_date}
              onChange={(e) => onFormChange((p) => ({ ...p, payment_date: e.target.value }))}
            />
          </FormField>
          <FormField label="Forma de pagamento" required>
            <Select
              value={form.payment_method}
              onChange={(e) => onFormChange((p) => ({ ...p, payment_method: e.target.value }))}
            >
              <option value="pix">Pix</option>
              <option value="dinheiro">Dinheiro</option>
              <option value="boleto">Boleto</option>
              <option value="cartao">Cartão</option>
            </Select>
          </FormField>
        </FormGrid>
        <FormField label="Observações" hint="Opcional — fica gravada no histórico da cobrança">
          <Textarea
            placeholder="Ex.: pago em dinheiro na loja, PIX recebido direto na conta…"
            value={form.notes}
            onChange={(e) => onFormChange((p) => ({ ...p, notes: e.target.value }))}
            className="min-h-[72px]"
          />
        </FormField>
        {billing?.boleto_ailos && (
          <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            Este boleto está registrado na Ailos. Dar baixa aqui marca a cobrança como paga no
            MasterSat, mas <strong>não cancela o título no banco</strong> — o boleto continua
            passível de pagamento até ser baixado/cancelado na Ailos.
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button onClick={onSave} disabled={saving}>
            {saving ? 'Registrando…' : 'Confirmar pagamento'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
