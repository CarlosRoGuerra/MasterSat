'use client';

import { useState } from 'react';
import { CheckCircle2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { SignaturePad } from '@/components/ui/signature-pad';
import { apiFetch } from '@/lib/api';
import { ServiceOrder, formatDateTimeLabel, parseError } from './types';

function SignatureBlock({
  title, signedAt, canEdit, saving, onSave,
}: {
  title: string;
  signedAt?: string | null;
  canEdit: boolean;
  saving: boolean;
  onSave: (dataUrl: string) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);

  if (signedAt) {
    return (
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/30">
        <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="h-5 w-5" />
          <p className="text-sm font-semibold">{title} — assinado</p>
        </div>
        <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-500">{formatDateTimeLabel(signedAt)}</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <SignaturePad label={title} onChange={setPending} disabled={!canEdit} />
      {canEdit && (
        <div className="mt-2 flex justify-end">
          <Button onClick={() => pending && onSave(pending)} disabled={!pending || saving} className="px-3 py-1.5 text-xs">
            {saving ? 'Salvando…' : 'Confirmar assinatura'}
          </Button>
        </div>
      )}
    </div>
  );
}

export function OsAssinaturasTab({
  order, canEdit, token, onError, onFeedback, onRefresh,
}: {
  order: ServiceOrder;
  canEdit: boolean;
  token: string;
  onError: (msg: string) => void;
  onFeedback: (msg: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [savingSigner, setSavingSigner] = useState<'technician' | 'client' | null>(null);

  async function submitSignature(signer: 'technician' | 'client', imageBase64: string) {
    setSavingSigner(signer);
    onError('');
    try {
      await apiFetch(`/service-orders/${order.id}/signature`, {
        method: 'POST',
        body: JSON.stringify({ signer, image_base64: imageBase64 }),
      }, token);
      onFeedback(signer === 'technician' ? 'Assinatura do técnico registrada.' : 'Assinatura do cliente registrada.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setSavingSigner(null);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Ambas as assinaturas, mais a descrição do serviço executado, são exigidas para concluir a ordem.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <SignatureBlock
          title={`Assinatura do técnico${order.technician_name ? ` — ${order.technician_name}` : ''}`}
          signedAt={order.technician_signed_at}
          canEdit={canEdit}
          saving={savingSigner === 'technician'}
          onSave={(dataUrl) => submitSignature('technician', dataUrl)}
        />
        <SignatureBlock
          title={`Assinatura do cliente${order.client_name ? ` — ${order.client_name}` : ''}`}
          signedAt={order.client_signed_at}
          canEdit={canEdit}
          saving={savingSigner === 'client'}
          onSave={(dataUrl) => submitSignature('client', dataUrl)}
        />
      </div>
    </div>
  );
}
