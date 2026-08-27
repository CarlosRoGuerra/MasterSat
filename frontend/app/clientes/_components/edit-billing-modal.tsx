import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { FormField, FormGrid } from '@/components/ui/form-field';
import type { BillingItem } from './types';

export type EditBillingForm = { amount: string; due_date: string; justification: string };

export function EditBillingModal({
  billing,
  form,
  saving,
  onFormChange,
  onClose,
  onSave,
}: {
  billing: BillingItem | null;
  form: EditBillingForm;
  saving: boolean;
  onFormChange: (updater: (prev: EditBillingForm) => EditBillingForm) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <Modal
      open={!!billing}
      onClose={onClose}
      title={billing ? `Alterar boleto #${billing.id}` : 'Alterar boleto'}
      subtitle="Alterações de valor e vencimento ficam registradas no histórico"
      size="md"
    >
      <div className="space-y-4">
        <FormGrid>
          <FormField label="Valor (R$)" required>
            <Input
              type="number"
              step="0.01"
              min="0.01"
              value={form.amount}
              onChange={(e) => onFormChange((p) => ({ ...p, amount: e.target.value }))}
            />
          </FormField>
          <FormField label="Vencimento" required>
            <Input
              type="date"
              value={form.due_date}
              onChange={(e) => onFormChange((p) => ({ ...p, due_date: e.target.value }))}
            />
          </FormField>
        </FormGrid>
        <FormField label="Justificativa" required hint="Obrigatória — fica gravada no histórico de operações">
          <Textarea
            placeholder="Ex.: negociação com o cliente, correção de valor…"
            value={form.justification}
            onChange={(e) => onFormChange((p) => ({ ...p, justification: e.target.value }))}
            className="min-h-[72px]"
          />
        </FormField>
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button onClick={onSave} disabled={saving}>
            {saving ? 'Salvando…' : 'Salvar alteração'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
