'use client';

import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';

/**
 * Confirmação para ações críticas disparadas pelo Assistente de Ações —
 * substitui window.confirm() só nesse fluxo (os demais window.confirm()
 * espalhados pelo app não são tocados por este componente). Cópia deve ser
 * sempre concreta ("Cancelar a OS #123 de João da Silva?"), nunca genérica
 * ("Tem certeza?") — quem chama monta o texto com os dados reais.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = 'Confirmar',
  danger = false,
  loading = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
  loading?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={loading}>
            Cancelar
          </Button>
          <Button type="button" variant={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={loading}>
            {loading ? 'Aguarde…' : confirmLabel}
          </Button>
        </div>
      }
    >
      <p className="text-sm text-slate-600 dark:text-slate-300">{description}</p>
    </Modal>
  );
}
