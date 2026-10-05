'use client';

import { FormEvent, useId, useRef, useState } from 'react';
import { AlertTriangle, CalendarClock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { TituloBancario } from '@/lib/titulo-bancario';

/** Boleto já registrado com a data errada: a API da Ailos não altera
 *  vencimento nem dá baixa, então a cobrança é substituída por uma nova, sem
 *  boleto, na data certa (POST /billings/{id}/corrigir-vencimento). O boleto
 *  antigo vai para a baixa pendente e o novo é emitido pelo botão de sempre. */
export function CorrigirVencimentoAction({
  billingId, billingStatus, dueDate, titulo, canEdit, disabled = false, onCorrigir,
}: {
  billingId: number;
  billingStatus: string;
  dueDate: string;
  titulo?: TituloBancario | null;
  canEdit: boolean;
  disabled?: boolean;
  onCorrigir: (dueDate: string, justificativa: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [novaData, setNovaData] = useState('');
  const [justification, setJustification] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const savingRef = useRef(false);
  const panelId = useId();
  const dateId = useId();
  const reasonId = useId();
  const eligible = canEdit && ['pendente', 'vencida'].includes(billingStatus)
    && (titulo?.estado === 'registrado' || titulo?.estado === 'baixado');
  const busy = disabled || saving;
  const valido = confirmed && !!novaData && novaData !== dueDate && justification.trim().length >= 3;

  if (!eligible) return null;

  function reset() {
    setOpen(false);
    setConfirmed(false);
    setNovaData('');
    setJustification('');
    setError('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || savingRef.current || !valido) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      await onCorrigir(novaData, justification.trim());
      reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível corrigir o vencimento.');
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  return (
    <>
      <Button type="button" variant="secondary" disabled={busy} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(true)}>
        <CalendarClock aria-hidden="true" className="h-4 w-4" />
        Corrigir vencimento
      </Button>
      {open && (
        <form id={panelId} onSubmit={submit} className="w-full space-y-4 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
          <div className="flex items-start gap-3">
            <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-amber-700 dark:text-amber-400" />
            <div className="space-y-1">
              <h4 className="font-semibold text-slate-900 dark:text-white">Corrigir o vencimento da cobrança #{billingId}</h4>
              <p className="text-sm text-slate-700 dark:text-slate-200">
                O boleto atual já está no banco e a Ailos não permite alterar a data. Esta cobrança será
                encerrada e uma nova, com o mesmo valor e a data correta, ficará pronta para você clicar em
                “Gerar boleto (Ailos)”. O boleto antigo entra na lista de baixa pendente: dê baixa nele no
                internet banking da Ailos e avise o cliente para desconsiderá-lo.
              </p>
            </div>
          </div>
          <div>
            <label htmlFor={dateId} className="mb-1 block text-sm font-medium text-slate-800 dark:text-slate-100">Vencimento correto</label>
            <input id={dateId} type="date" value={novaData} onChange={event => setNovaData(event.target.value)} disabled={busy} required className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white" />
          </div>
          <div>
            <label htmlFor={reasonId} className="mb-1 block text-sm font-medium text-slate-800 dark:text-slate-100">Justificativa</label>
            <textarea id={reasonId} value={justification} onChange={event => setJustification(event.target.value)} disabled={busy} required minLength={3} rows={2} className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white" />
          </div>
          <label className="flex items-start gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
            <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} required className="mt-0.5 h-4 w-4 shrink-0 accent-brand-500" />
            Entendi que o boleto atual continua pagável até a baixa no internet banking da Ailos.
          </label>
          {error && <p role="alert" className="text-sm text-rose-700 dark:text-rose-400">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={saving} onClick={reset}>Cancelar</Button>
            <Button type="submit" disabled={busy || !valido}>{saving ? 'Corrigindo…' : 'Corrigir vencimento'}</Button>
          </div>
        </form>
      )}
    </>
  );
}
