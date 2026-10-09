import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { motivoBloqueioExclusao } from '@/lib/exclusao-cobranca';
import type { BillingItem } from './types';

export type EditBillingForm = { amount: string; due_date: string; justification: string };
export type BillingAction = 'save' | 'cancel' | 'delete' | null;

export function EditBillingModal({
  billing,
  form,
  action,
  error,
  notice,
  canManage,
  onFormChange,
  onClose,
  onSave,
  onCancel,
  onDelete,
}: {
  billing: BillingItem | null;
  form: EditBillingForm;
  action: BillingAction;
  error: string;
  notice: string;
  canManage: boolean;
  onFormChange: (updater: (prev: EditBillingForm) => EditBillingForm) => void;
  onClose: () => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const busy = action !== null;
  const editable = !!billing && canManage && ['pendente', 'vencida'].includes(billing.status);
  const deleteBlocked = billing ? motivoBloqueioExclusao(billing) : null;
  const close = () => { if (!busy) onClose(); };
  return (
    <Modal
      open={!!billing}
      onClose={close}
      title={billing ? `Alterar boleto #${billing.id}` : 'Alterar boleto'}
      subtitle="Alterações de valor e vencimento ficam registradas no histórico"
      size="md"
    >
      <div className="space-y-4">
        {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">{error}</p>}
        {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">{notice}</p>}
        {billing?.status === 'paga' && <p className="text-sm text-slate-500">Boletos pagos não podem ser alterados, cancelados ou excluídos.</p>}
        {billing?.status === 'cancelada' && !notice && <p className="text-sm text-slate-500">Boleto cancelado no sistema.</p>}
        {billing?.titulo_bancario?.baixa_status === 'pendente' && (
          <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
            A baixa bancária está pendente. Cancelar no sistema não cancela o boleto no banco.
          </p>
        )}
        <FormGrid>
          <FormField label="Valor (R$)" required>
            <Input
              type="number"
              step="0.01"
              min="0.01"
              value={form.amount}
              disabled={busy || !editable}
              onChange={(e) => onFormChange((p) => ({ ...p, amount: e.target.value }))}
            />
          </FormField>
          <FormField label="Vencimento" required>
            <Input
              type="date"
              value={form.due_date}
              disabled={busy || !editable}
              onChange={(e) => onFormChange((p) => ({ ...p, due_date: e.target.value }))}
            />
          </FormField>
        </FormGrid>
        {editable && <FormField label="Justificativa" required hint="Obrigatória para alterar ou cancelar. O motivo do cancelamento fica nas observações da cobrança.">
          <Textarea
            placeholder="Ex.: negociação com o cliente, correção de valor…"
            value={form.justification}
            disabled={busy}
            onChange={(e) => onFormChange((p) => ({ ...p, justification: e.target.value }))}
            className="min-h-[72px]"
          />
        </FormField>}
        {canManage && billing?.status !== 'paga' && (
          <div className="space-y-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Cancelar mantém a cobrança no histórico. Excluir retira a cobrança da carteira, preservando o registro no sistema.
            </p>
            {deleteBlocked && <p className="text-xs text-slate-500 dark:text-slate-400">{deleteBlocked}</p>}
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={onCancel} disabled={busy || !editable}>
                {action === 'cancel' ? 'Cancelando…' : 'Cancelar boleto'}
              </Button>
              <Button variant="danger" onClick={onDelete} disabled={busy || !!deleteBlocked}>
                {action === 'delete' ? 'Excluindo…' : 'Excluir boleto'}
              </Button>
            </div>
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={close} disabled={busy}>Fechar janela</Button>
          {editable && <Button onClick={onSave} disabled={busy}>
            {action === 'save' ? 'Salvando…' : 'Salvar alteração'}
          </Button>}
        </div>
      </div>
    </Modal>
  );
}
