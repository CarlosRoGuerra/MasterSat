'use client';

import { FormEvent, useId, useRef, useState } from 'react';
import { AlertTriangle, LockKeyhole } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { TituloBancario } from '@/lib/titulo-bancario';

export function BoletoReleaseAction({
  billingId, billingStatus, titulo, isAdmin, disabled = false, onRelease,
}: {
  billingId: number;
  billingStatus: string;
  titulo?: TituloBancario | null;
  isAdmin: boolean;
  disabled?: boolean;
  onRelease: (justificativa: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [justification, setJustification] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const savingRef = useRef(false);
  const panelId = useId();
  const reasonId = useId();
  const eligible = isAdmin && titulo?.estado === 'desfecho_desconhecido'
    && ['pendente', 'vencida'].includes(billingStatus);
  const busy = disabled || saving;

  if (!eligible) return null;

  function reset() {
    setOpen(false);
    setConfirmed(false);
    setJustification('');
    setError('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!eligible || busy || savingRef.current || !confirmed || justification.trim().length < 3) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      await onRelease(justification.trim());
      reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível liberar a cobrança.');
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  return (
    <>
      <Button type="button" variant="secondary" disabled={busy} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(true)}>
        <LockKeyhole aria-hidden="true" className="h-4 w-4" />
        Liberar nova tentativa
      </Button>
      {open && (
        <form id={panelId} onSubmit={submit} className="w-full space-y-4 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
          <div className="flex items-start gap-3">
            <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-amber-700 dark:text-amber-400" />
            <div className="space-y-1">
              <h4 className="font-semibold text-slate-900 dark:text-white">Confira no banco antes de liberar a cobrança #{billingId}</h4>
              <p className="text-sm text-slate-700 dark:text-slate-200">
                Consulte o Internet Banking da Ailos e confira o pagador, o valor e o vencimento.
                Se o boleto já constar no banco, use “Consultar desfecho na Ailos” para recuperar o registro.
                Liberar e emitir novamente um boleto que já existe pode gerar uma cobrança duplicada.
              </p>
            </div>
          </div>
          <label className="flex items-start gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
            <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} required className="mt-0.5 h-4 w-4 shrink-0 accent-brand-500" />
            Consultei o Internet Banking da Ailos e confirmei que este boleto não foi emitido.
          </label>
          <div>
            <label htmlFor={reasonId} className="mb-1 block text-sm font-medium text-slate-800 dark:text-slate-100">Justificativa da liberação</label>
            <textarea id={reasonId} value={justification} onChange={event => setJustification(event.target.value)} disabled={busy} required minLength={3} maxLength={500} rows={3} placeholder="Informe quando e como conferiu a ausência do boleto no banco." className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-white" />
          </div>
          <p className="text-xs text-slate-600 dark:text-slate-300">A liberação ficará no histórico com seu usuário e justificativa. Depois de liberar, clique em “Gerar boleto (Ailos)” para tentar a emissão.</p>
          {error && <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" disabled={busy} onClick={reset}>Voltar</Button>
            <Button type="submit" disabled={busy || !confirmed || justification.trim().length < 3}>{saving ? 'Liberando…' : 'Confirmar liberação'}</Button>
          </div>
        </form>
      )}
    </>
  );
}
