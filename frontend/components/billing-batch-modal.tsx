'use client';

import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { billingBatchSummary, type BillingBatchAction, type BillingBatchResult } from '@/lib/billing-batch';
import { statusLabel } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Textarea } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';

export function BillingBatchModal({ action, ids, token, onClose, onCompleted }: {
  action: BillingBatchAction | null;
  ids: number[];
  token: string | null;
  onClose: () => void;
  onCompleted: (result: BillingBatchResult) => Promise<void>;
}) {
  const [preview, setPreview] = useState<BillingBatchResult | null>(null);
  const [result, setResult] = useState<BillingBatchResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [reason, setReason] = useState('');
  const [bankConfirmed, setBankConfirmed] = useState(false);
  const [periodConfirmed, setPeriodConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const inflight = useRef(false);
  const selection = ids.join(',');

  useEffect(() => {
    setReason('');
  }, [action, selection]);

  useEffect(() => {
    setPreview(null);
    setResult(null);
    setError('');
    setBankConfirmed(false);
    setPeriodConfirmed(false);
    if (!action || !token || !ids.length) return;
    let active = true;
    setChecking(true);
    apiFetch<BillingBatchResult>('/billings/lote/acoes', {
      method: 'POST', body: JSON.stringify({ action, billing_ids: ids, simular: true }),
    }, token)
      .then((data) => { if (active) setPreview(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Falha ao conferir boletos.'); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
    // A seleção capturada ao abrir o lote determina a consulta.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, selection, token, retry]);

  const close = () => { if (!inflight.current) onClose(); };
  const data = result ?? preview;
  const bankCount = data?.itens.filter((item) => item.baixa_bancaria_pendente).length ?? 0;
  const periodCount = data?.itens.filter((item) => item.libera_competencia).length ?? 0;
  const ready = !!preview?.aplicados && reason.trim().length >= 3
    && (!bankCount || bankConfirmed) && (!periodCount || periodConfirmed);

  async function apply() {
    if (!action || !token || !ready || !preview || inflight.current || result) return;
    inflight.current = true;
    setApplying(true);
    setError('');
    try {
      const response = await apiFetch<BillingBatchResult>('/billings/lote/acoes', {
        method: 'POST', body: JSON.stringify({ action, billing_ids: ids, simular: false,
          revisao: preview.revisao, reason: reason.trim(),
          confirmar_boleto_ailos: bankConfirmed,
          confirmar_liberacao_competencia: periodConfirmed }),
      }, token);
      if (response.simulacao !== false || response.action !== action) {
        throw new Error('O servidor não confirmou a aplicação do lote. Confira a carteira antes de repetir.');
      }
      setResult(response);
      try {
        await onCompleted(response);
      } catch (err) {
        setError(`Operação concluída. Falha ao atualizar a tela: ${err instanceof Error ? err.message : 'tente atualizar a carteira.'}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível concluir o lote.');
      if ((err as { detail?: { code?: string } }).detail?.code === 'revisao_desatualizada') setPreview(null);
    } finally {
      inflight.current = false;
      setApplying(false);
    }
  }

  return <Modal open={!!action} onClose={close}
    title={action === 'excluir' ? 'Excluir boletos em lote' : 'Cancelar boletos em lote'} size="md"
    description={action === 'excluir'
      ? 'Exclusão de cobranças canceladas sem histórico bancário. O registro fica preservado no sistema.'
      : 'Cancelamento das cobranças em aberto. As competências continuam ocupadas.'}
    footer={<div className="flex flex-wrap justify-end gap-2">
      <Button variant="secondary" disabled={applying} onClick={close}>{result ? 'Fechar resultado' : 'Voltar'}</Button>
      {!result && !preview && !checking && <Button variant="secondary" onClick={() => setRetry((value) => value + 1)}>Conferir novamente</Button>}
      {!result && <Button variant="danger" disabled={checking || applying || !ready} onClick={apply}>
        {applying ? 'Aplicando…' : `${action === 'excluir' ? 'Excluir' : 'Cancelar'} ${preview?.aplicados ?? 0} boleto(s)`}
      </Button>}
    </div>}>
    <div className="space-y-4">
      {checking && <p role="status">Conferindo os {ids.length} boletos selecionados…</p>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">{error}</p>}
      {result && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">{billingBatchSummary(result)}</p>}
      {data && <>
        {!result && <p className="text-sm">{data.aplicados} apto(s) · {data.ignorados} ignorado(s) · {data.total_enviados} selecionado(s)</p>}
        {!!bankCount && <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          <p>{bankCount} boleto(s) continuam pagáveis no banco até a baixa bancária. O cancelamento no sistema mantém a baixa pendente.</p>
          {!result && <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={bankConfirmed} disabled={applying} onChange={(event) => setBankConfirmed(event.target.checked)} />
            Estou ciente de que preciso providenciar a baixa dos títulos no banco.
          </label>}
        </div>}
        {!!periodCount && <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          <p>A exclusão de {periodCount} mensalidade(s) libera os meses para nova cobrança pelo fechamento.</p>
          {!result && <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={periodConfirmed} disabled={applying} onChange={(event) => setPeriodConfirmed(event.target.checked)} />
            Confirmo a liberação dos meses para nova cobrança.
          </label>}
        </div>}
        {!result && <FormField label="Justificativa" required hint="Mínimo de 3 caracteres. Fica registrada em cada cobrança processada.">
          <Textarea value={reason} maxLength={500} disabled={applying} onChange={(event) => setReason(event.target.value)} />
        </FormField>}
        <ul className="max-h-64 divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-700" aria-label="Resultado por boleto">
          {data.itens.map((item) => <li key={item.billing_id} className="space-y-1 p-3">
            <p className="font-medium">#{item.billing_id}{item.title ? ` · ${item.title}` : ''}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {item.situacao === 'ignorado' ? `Ignorado: ${item.motivo}`
                : result ? (action === 'excluir' ? 'Excluído da carteira' : 'Cancelado no sistema')
                  : `Será ${action === 'excluir' ? 'excluído' : 'cancelado'} · ${statusLabel(item.status ?? '')}`}
            </p>
            {item.nosso_numero && <p className="font-mono text-xs">Nosso número: {item.nosso_numero}</p>}
          </li>)}
        </ul>
      </>}
    </div>
  </Modal>;
}
