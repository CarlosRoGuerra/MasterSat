'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { formatCurrency, formatDate } from '@/lib/format';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';

type Lote = {
  id: number;
  mes_servico: string;
  criado_em: string;
  total_titulos: number;
};

type Item = {
  billing_id: number;
  cliente: string;
  email: string | null;
  valor: number;
  vencimento: string | null;
  documentos: string[];
  emitir_nfse: string | null;
  estado: 'pronto' | 'bloqueado' | 'enviado' | 'processando' | 'desconhecido';
  motivo: string | null;
};

type Previa = {
  id: number;
  mes_servico: string;
  total: number;
  prontos: number;
  enviados: number;
  bloqueados: number;
  indeterminados: number;
  itens: Item[];
};

function mensagemErro(err: unknown) {
  return err instanceof Error ? err.message : 'Falha ao consultar o lote.';
}

function rotuloMes(value: string) {
  const [ano, mes] = value.split('-');
  return `${mes}/${ano}`;
}

export function ClosureDeliveryModal({
  open, token, onClose,
}: {
  open: boolean;
  token: string | null;
  onClose: () => void;
}) {
  const [lotes, setLotes] = useState<Lote[]>([]);
  const [loteId, setLoteId] = useState('');
  const [previa, setPrevia] = useState<Previa | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  async function atualizarPrevia() {
    if (!token || !loteId || sending) return;
    setLoading(true);
    setError('');
    try {
      setPrevia(await apiFetch<Previa>(`/billing-closure/lotes/${loteId}`, {}, token));
    } catch (err) {
      setError(mensagemErro(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open || !token) return;
    setLoading(true);
    setError('');
    apiFetch<Lote[]>('/billing-closure/lotes', {}, token)
      .then(setLotes)
      .catch(err => setError(mensagemErro(err)))
      .finally(() => setLoading(false));
  }, [open, token]);

  useEffect(() => {
    if (!open || !token || !loteId) {
      setPrevia(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError('');
    setFeedback('');
    apiFetch<Previa>(`/billing-closure/lotes/${loteId}`, {}, token)
      .then(data => { if (active) setPrevia(data); })
      .catch(err => { if (active) setError(mensagemErro(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, token, loteId]);

  async function enviarLote() {
    if (!token || !previa || sending) return;
    const prontos = previa.itens.filter(item => item.estado === 'pronto');
    if (!prontos.length) return;
    if (!window.confirm(
      `Enviar por e-mail ${prontos.length} cobrança(s) do fechamento #${previa.id}? ` +
      `Cada responsável receberá o boleto e, quando seu cadastro exigir, também a NFS-e.`,
    )) return;

    setSending(true);
    setError('');
    setFeedback('');
    let enviados = 0;
    const falhas: string[] = [];
    try {
      for (const [index, item] of prontos.entries()) {
        setProgress(`${index + 1}/${prontos.length} · ${item.cliente}`);
        try {
          await apiFetch(`/billing-closure/lotes/${previa.id}/enviar/${item.billing_id}`, {
            method: 'POST',
          }, token);
          enviados += 1;
          setPrevia(current => current ? {
            ...current,
            itens: current.itens.map(row => row.billing_id === item.billing_id ? { ...row, estado: 'enviado' } : row),
          } : current);
        } catch (err) {
          falhas.push(`#${item.billing_id} ${item.cliente}: ${mensagemErro(err)}`);
        }
      }
      setFeedback(`${enviados} envio(s) concluído(s)${falhas.length ? `; ${falhas.length} não concluído(s)` : ''}.`);
      if (falhas.length) setError(falhas.slice(0, 8).join(' · ') + (falhas.length > 8 ? ` · e mais ${falhas.length - 8}` : ''));
      setPrevia(await apiFetch<Previa>(`/billing-closure/lotes/${previa.id}`, {}, token));
    } catch (err) {
      setError(mensagemErro(err));
    } finally {
      setSending(false);
      setProgress('');
    }
  }

  return (
    <Modal open={open} onClose={() => { if (!sending) onClose(); }} title="Enviar fechamento por e-mail"
      description="Escolha um lote de fechamento. A lista inclui todos os títulos dessa execução, independentemente da paginação da carteira."
      size="2xl">
      <div className="space-y-4">
        <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
          Lote de fechamento
          <select value={loteId} disabled={sending || loading}
            onChange={e => setLoteId(e.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900">
            <option value="">Selecione o fechamento</option>
            {lotes.map(lote => <option key={lote.id} value={lote.id}>
              #{lote.id} · serviço {rotuloMes(lote.mes_servico)} · {lote.total_titulos} título(s)
            </option>)}
          </select>
        </label>
        {lotes.length === 0 && !loading && <p className="text-sm text-slate-500">Nenhum fechamento com lote registrado. Fechamentos anteriores a esta função não tinham identificador de execução.</p>}
        {loading && <p className="text-sm text-slate-500">Carregando fechamento…</p>}
        {previa && !loading && <>
          <div className="flex flex-wrap gap-2 text-sm">
            <span className="rounded-lg bg-slate-100 px-3 py-2 dark:bg-slate-800">{previa.total} título(s)</span>
            <span className="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">{previa.prontos} pronto(s)</span>
            <span className="rounded-lg bg-blue-50 px-3 py-2 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300">{previa.enviados} enviado(s)</span>
            <span className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">{previa.bloqueados} pendente(s)</span>
            {previa.indeterminados > 0 && <span className="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{previa.indeterminados} a conferir</span>}
          </div>
          <div className="max-h-[50vh] overflow-auto rounded-xl border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-[780px] text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-200">
                <tr><th className="px-3 py-2">Cobrança</th><th className="px-3 py-2">Responsável / e-mail</th><th className="px-3 py-2">Vencimento</th><th className="px-3 py-2">Valor</th><th className="px-3 py-2">Anexos</th><th className="px-3 py-2">Situação</th></tr>
              </thead>
              <tbody>
                {previa.itens.map(item => <tr key={item.billing_id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="px-3 py-2">#{item.billing_id}</td>
                  <td className="px-3 py-2"><strong className="block font-medium">{item.cliente}</strong><span className="text-slate-500">{item.email || 'Sem e-mail'}</span></td>
                  <td className="px-3 py-2">{item.vencimento ? formatDate(item.vencimento) : '—'}</td>
                  <td className="px-3 py-2">{formatCurrency(item.valor)}</td>
                  <td className="px-3 py-2">{item.documentos.join(' + ')}</td>
                  <td className="px-3 py-2"><span className="font-medium">{item.estado === 'pronto' ? 'Pronto' : item.estado === 'enviado' ? 'Enviado' : item.estado === 'bloqueado' ? 'Pendente' : 'Conferir'}</span>{item.motivo && <span className="block text-amber-700">{item.motivo}</span>}</td>
                </tr>)}
              </tbody>
            </table>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={atualizarPrevia} disabled={sending || loading}>
              Atualizar conferência
            </Button>
            <Button type="button" onClick={enviarLote} disabled={sending || previa.prontos === 0}>
              {sending ? `Enviando ${progress}` : `Enviar fechamento: ${previa.prontos} pronto(s)`}
            </Button>
          </div>
        </>}
        {feedback && <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{feedback}</p>}
        {error && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
      </div>
    </Modal>
  );
}
