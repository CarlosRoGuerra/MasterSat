import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { FormField, FormGrid } from '@/components/ui/form-field';

export type UnifyForm = { due_date: string; amount: string; notes: string };

export function UnifyBillingModal({
  open,
  selectedCount,
  form,
  saving,
  onFormChange,
  onClose,
  onSave,
}: {
  open: boolean;
  selectedCount: number;
  form: UnifyForm;
  saving: boolean;
  onFormChange: (updater: (prev: UnifyForm) => UnifyForm) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Unificar ${selectedCount} boletos em um único`}
      subtitle="As cobranças originais são canceladas e substituídas por um boleto avulso"
      size="md"
    >
      <div className="space-y-4">
        <FormGrid>
          <FormField label="Vencimento do boleto único" required>
            <Input
              type="date"
              value={form.due_date}
              onChange={(e) => onFormChange((p) => ({ ...p, due_date: e.target.value }))}
            />
          </FormField>
          <FormField label="Valor (R$)" hint="Pré-preenchido com o total atualizado (com juros) — ajuste para dar desconto ou arredondar">
            <Input
              type="number"
              step="0.01"
              min="0.01"
              value={form.amount}
              onChange={(e) => onFormChange((p) => ({ ...p, amount: e.target.value }))}
            />
          </FormField>
        </FormGrid>
        <FormField label="Observações (opcional)">
          <Textarea
            placeholder="Ex.: negociação com o cliente em 14/07…"
            value={form.notes}
            onChange={(e) => onFormChange((p) => ({ ...p, notes: e.target.value }))}
            className="min-h-[64px]"
          />
        </FormField>
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button onClick={onSave} disabled={saving || !form.due_date}>
            {saving ? 'Unificando…' : 'Criar boleto único'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
