'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { statusLabel } from '@/components/ui/badge';

export type AcaoLote = 'status' | 'excluir';

export type AcaoLoteResultado = {
  simulacao: boolean;
  total_enviados: number;
  aplicados: number;
  ignorados: number;
  itens: { tracker_id: number; imei?: string | null; situacao: 'aplicado' | 'ignorado'; motivo?: string | null }[];
};

export type AcaoLoteCorpo = { ids: number[]; simular: boolean; status?: string; notes?: string | null };

/** "Instalado" depende da vinculação a um veículo, então não entra em lote. */
export const STATUS_EM_LOTE = ['em_estoque', 'em_manutencao', 'extraviado', 'descartado'] as const;

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

function erro(err: unknown) {
  return err instanceof Error ? err.message : 'Ocorreu um erro inesperado.';
}

/** Barra que aparece com rastreadores selecionados na listagem: alterar o
 *  status ou excluir todos de uma vez (POST /trackers/lote/status e
 *  /trackers/lote/excluir). Antes de aplicar, o backend simula e diz quais
 *  ficam de fora — instalados, com contrato ativo e, na exclusão, os que não
 *  estão extraviados ou em manutenção. */
export function AcoesEmLote({
  ids, onLimpar, onExecutar, onConcluido,
}: {
  ids: number[];
  onLimpar: () => void;
  onExecutar: (acao: AcaoLote, corpo: AcaoLoteCorpo) => Promise<AcaoLoteResultado>;
  onConcluido: (mensagem: string) => void;
}) {
  const [acao, setAcao] = useState<AcaoLote | null>(null);
  const [status, setStatus] = useState('');
  const [notes, setNotes] = useState('');
  const [previa, setPrevia] = useState<AcaoLoteResultado | null>(null);
  const [conferindo, setConferindo] = useState(false);
  const [aplicando, setAplicando] = useState(false);
  const [falha, setFalha] = useState('');

  // Confere com o backend sempre que a ação (ou o status escolhido) muda.
  useEffect(() => {
    setPrevia(null);
    setFalha('');
    if (!acao || (acao === 'status' && !status)) return;
    let ativo = true;
    setConferindo(true);
    onExecutar(acao, { ids, simular: true, ...(acao === 'status' ? { status } : {}) })
      .then((r) => { if (ativo) setPrevia(r); })
      .catch((err) => { if (ativo) setFalha(erro(err)); })
      .finally(() => { if (ativo) setConferindo(false); });
    return () => { ativo = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [acao, status, ids.join(',')]);

  if (ids.length === 0) return null;

  function abrir(nova: AcaoLote) {
    setStatus('');
    setNotes('');
    setAcao(nova);
  }

  async function aplicar() {
    if (!acao || !previa?.aplicados) return;
    setAplicando(true);
    setFalha('');
    try {
      const r = await onExecutar(acao, {
        ids, simular: false,
        ...(acao === 'status' ? { status, notes: notes.trim() || null } : {}),
      });
      const feito = acao === 'status'
        ? `Status de ${r.aplicados} rastreador(es) alterado para "${statusLabel(status)}".`
        : `${r.aplicados} rastreador(es) excluído(s).`;
      setAcao(null);
      onConcluido(r.ignorados ? `${feito} ${r.ignorados} ficaram de fora.` : feito);
    } catch (err) {
      setFalha(erro(err));
    } finally {
      setAplicando(false);
    }
  }

  const ignorados = previa?.itens.filter((i) => i.situacao === 'ignorado') ?? [];
  const excluir = acao === 'excluir';

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3 rounded-2xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm dark:border-brand-900/50 dark:bg-brand-950/30">
        <span className="font-semibold text-slate-800 dark:text-slate-100">{ids.length} selecionado(s)</span>
        <button type="button" onClick={onLimpar} className="text-xs font-medium text-slate-500 underline-offset-2 hover:underline">
          Limpar seleção
        </button>
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => abrir('status')}>Alterar status</Button>
          <Button variant="danger" className="px-3 py-1.5 text-xs" onClick={() => abrir('excluir')}>Excluir</Button>
        </div>
      </div>

      <Modal
        open={acao !== null}
        onClose={() => !aplicando && setAcao(null)}
        title={excluir ? 'Excluir rastreadores' : 'Alterar status'}
        description={excluir
          ? 'Só é possível excluir rastreador extraviado ou em manutenção, sem veículo e sem contrato ativo.'
          : 'Rastreador instalado ou com contrato ativo fica de fora: o status dele muda pela desinstalação.'}
        size="md"
        footer={(
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAcao(null)} disabled={aplicando}>Cancelar</Button>
            <Button
              variant={excluir ? 'danger' : 'primary'}
              onClick={aplicar}
              disabled={aplicando || conferindo || !previa?.aplicados}
            >
              {aplicando ? 'Aplicando…'
                : excluir ? `Excluir ${previa?.aplicados ?? 0} rastreador(es)`
                  : `Alterar ${previa?.aplicados ?? 0} rastreador(es)`}
            </Button>
          </div>
        )}
      >
        <div className="space-y-4">
          {!excluir && (
            <>
              <div>
                <label htmlFor="lote-status" className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Novo status</label>
                <select id="lote-status" className={fieldClass} value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="">Selecione…</option>
                  {STATUS_EM_LOTE.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="lote-obs" className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Observação (vai para o histórico)</label>
                <input id="lote-obs" className={fieldClass} value={notes} maxLength={500}
                       placeholder="Ex.: inventário de outubro" onChange={(e) => setNotes(e.target.value)} />
              </div>
            </>
          )}

          {falha && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{falha}</p>}
          {conferindo && <p className="text-sm text-slate-500">Conferindo a seleção…</p>}

          {previa && (
            <div className="rounded-2xl border border-slate-200 dark:border-slate-700">
              <p className="flex flex-wrap gap-4 border-b border-slate-100 px-4 py-3 text-sm dark:border-slate-800">
                <span className="text-emerald-600 dark:text-emerald-400">
                  {previa.aplicados} {excluir ? 'serão excluído(s)' : 'serão alterado(s)'}
                </span>
                {previa.ignorados > 0 && <span className="text-amber-600 dark:text-amber-400">{previa.ignorados} ficam de fora</span>}
              </p>
              {ignorados.length > 0 && (
                <ul className="max-h-56 divide-y divide-slate-100 overflow-y-auto text-sm dark:divide-slate-800">
                  {ignorados.map((i) => (
                    <li key={i.tracker_id} className="flex items-center justify-between gap-3 px-4 py-2">
                      <span className="font-mono text-slate-700 dark:text-slate-200">{i.imei || `#${i.tracker_id}`}</span>
                      <span className="text-right text-xs text-slate-500">{i.motivo}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
