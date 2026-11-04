'use client';

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Download, ListChecks, Loader2, RefreshCw } from 'lucide-react';
import { API_URL, apiFetch } from '@/lib/api';
import { entregarArquivo } from '@/lib/arquivo';
import { caminhoPdfCarne, nomeBancoBoleto, type BancoBoleto } from '@/lib/banco-boleto';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';

/**
 * Acompanhamento em tempo real do registro de um carnê no banco: progresso
 * agregado + status por parcela, retry automático por ~1min e, se ainda não
 * terminou, retry individual/em massa manual + baixar parcial.
 *
 * Compartilhado entre a tela Financeiro (gerar carnê), o cadastro de veículo
 * (plano com cobrança em carnê) e qualquer outro fluxo que precise gerar ou
 * reabrir o acompanhamento de um carnê — a lógica de polling/retry vive aqui
 * uma única vez.
 */

export type CarneTrackFase = 'registrando' | 'acompanhando' | 'completo' | 'erro-registro' | 'aguardando-manual';

export type ParcelaTrack = {
  billing_id: number;
  numero_parcela: number;
  vencimento: string | null;
  valor: number | null;
  status: 'processando' | 'registrado' | 'erro';
  erro: string | null;
};

type CarneTrack = {
  banco: BancoBoleto;
  ids: number[];
  fase: CarneTrackFase;
  loteId: number | null;
  ticket: string | null;
  prontas: number;
  total: number;
  erro: string;
  parcelas: ParcelaTrack[];
  retryingId: number | null;
  registrandoPendentes: boolean;
};

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function formatDate(value: string) {
  try { return new Date(value.length <= 10 ? `${value}T00:00:00` : value).toLocaleDateString('pt-BR'); }
  catch { return value; }
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
}

const CARNE_POLL_MS = 3000;
const CARNE_TENTATIVAS_AUTO = 20; // ~1 min de retry automático antes de pedir ação manual

export function useCarneTracking(token: string | null, onCompleted?: (info: { loteId: number; total: number; banco: BancoBoleto }) => void) {
  const [track, setTrack] = useState<CarneTrack | null>(null);
  // Cada operação tem identidade própria, inclusive ao reabrir o mesmo ticket.
  const operacaoRef = useRef(0);
  useEffect(() => () => { operacaoRef.current++; }, []);

  async function baixarPdf(loteId: number, banco: BancoBoleto = 'ailos', parcial = false) {
    const resp = await fetch(`${API_URL.replace(/\/+$/, '')}${caminhoPdfCarne(loteId, banco, parcial)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) {
      let detalhe = `Erro ${resp.status}`;
      try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
      throw new Error(detalhe);
    }
    entregarArquivo(await resp.blob(), `carne-${banco}-${loteId}.pdf`, { emNovaAba: true });
  }

  type StatusLote = { status: string; total: number; prontas: number; parcelas: ParcelaTrack[] };

  async function acompanhar(ticket: string, loteId: number, tentativa: number, banco: BancoBoleto, operacao: number) {
    if (operacaoRef.current !== operacao) return;
    try {
      const st = await apiFetch<StatusLote>(`/${banco}/lotes/${ticket}`, {}, token!);
      if (operacaoRef.current !== operacao) return;
      setTrack(prev => prev && ({ ...prev, prontas: st.prontas, total: st.total || prev.total, parcelas: st.parcelas || prev.parcelas }));
      if (st.status === 'completed') {
        setTrack(prev => prev && ({ ...prev, fase: 'completo' }));
        try { await baixarPdf(loteId, banco); }
        catch (err) {
          if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, erro: parseError(err) }));
        }
        if (operacaoRef.current === operacao) onCompleted?.({ loteId, total: st.total, banco });
        return;
      }
      if (st.status === 'partial') {
        setTrack(prev => prev && ({ ...prev, fase: 'aguardando-manual' }));
        return;
      }
    } catch (err) {
      if (operacaoRef.current !== operacao) return;
      setTrack(prev => prev && ({ ...prev, erro: parseError(err) }));
    }
    if (tentativa + 1 >= CARNE_TENTATIVAS_AUTO) {
      setTrack(prev => prev && ({ ...prev, fase: 'aguardando-manual' }));
      return;
    }
    setTimeout(() => acompanhar(ticket, loteId, tentativa + 1, banco, operacao), CARNE_POLL_MS);
  }

  function iniciarTrack(ids: number[], banco: BancoBoleto, loteId: number | null = null, ticket: string | null = null): CarneTrack {
    return { ids, banco, fase: ticket ? 'acompanhando' : 'registrando', loteId, ticket, prontas: 0, total: ids.length, erro: '', parcelas: [], retryingId: null, registrandoPendentes: false };
  }

  async function iniciar(ids: number[], banco: BancoBoleto = 'ailos') {
    if (!token) return;
    const operacao = ++operacaoRef.current;
    setTrack(iniciarTrack(ids, banco));
    try {
      const lote = await apiFetch<{ id: number; ticket: string; status: string }>(
        `/${banco}/carne/lote`,
        { method: 'POST', body: JSON.stringify({ billing_ids: ids }) }, token,
      );
      if (operacaoRef.current !== operacao) return;
      setTrack(iniciarTrack(ids, banco, lote.id, lote.ticket));
      // Ailos precisa completar parcelas que não foram efetivadas pelo lote.
      // O Sicredi já registra individualmente no servidor: primeiro consultamos,
      // sem reenviar parcelas em andamento nem desfechos desconhecidos.
      if (banco === 'ailos') {
        try { await apiFetch(`/ailos/lotes/${lote.id}/registrar-pendentes`, { method: 'POST' }, token); }
        catch { /* A consulta apresenta as pendências e as ações disponíveis. */ }
      }
      await acompanhar(lote.ticket, lote.id, 0, banco, operacao);
    } catch (err) {
      if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, fase: 'erro-registro', erro: parseError(err) }));
    }
  }

  async function abrirExistente(loteId: number, ticket: string, banco: BancoBoleto = 'ailos') {
    if (!token) return;
    const operacao = ++operacaoRef.current;
    setTrack(iniciarTrack([], banco, loteId, ticket));
    try {
      const st = await apiFetch<StatusLote>(`/${banco}/lotes/${ticket}`, {}, token);
      if (operacaoRef.current !== operacao) return;
      setTrack(prev => prev && ({ ...prev, fase: st.status === 'completed' ? 'completo' : st.status === 'partial' ? 'aguardando-manual' : 'acompanhando', prontas: st.prontas, total: st.total, parcelas: st.parcelas }));
      if (st.status !== 'completed' && st.status !== 'partial') await acompanhar(ticket, loteId, 0, banco, operacao);
    } catch (err) {
      // O lote já existe: a próxima ação consulta o ticket, não cria outro lote.
      if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, fase: 'aguardando-manual', erro: parseError(err) }));
    }
  }

  async function registrarParcela(billingId: number) {
    if (!track?.loteId || !track.ticket || !token || track.retryingId || track.registrandoPendentes) return;
    const { loteId, ticket, banco } = track;
    const operacao = ++operacaoRef.current;
    setTrack(prev => prev && ({ ...prev, fase: 'acompanhando', retryingId: billingId, erro: '' }));
    try { await apiFetch(`/${banco}/lotes/${loteId}/parcelas/${billingId}/registrar`, { method: 'POST' }, token); }
    catch (err) { if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, erro: parseError(err) })); }
    finally { if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, retryingId: null })); }
    await acompanhar(ticket, loteId, 0, banco, operacao);
  }

  async function registrarPendentes() {
    if (!track?.loteId || !track.ticket || !token || track.retryingId || track.registrandoPendentes) return;
    const { loteId, ticket, banco } = track;
    const operacao = ++operacaoRef.current;
    setTrack(prev => prev && ({ ...prev, fase: 'acompanhando', registrandoPendentes: true, erro: '' }));
    try { await apiFetch(`/${banco}/lotes/${loteId}/registrar-pendentes`, { method: 'POST' }, token); }
    catch (err) { if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, erro: parseError(err) })); }
    finally { if (operacaoRef.current === operacao) setTrack(prev => prev && ({ ...prev, registrandoPendentes: false })); }
    await acompanhar(ticket, loteId, 0, banco, operacao);
  }

  function verificarNovamente() {
    if (!track?.ticket || !track.loteId) return;
    const operacao = ++operacaoRef.current;
    setTrack(prev => prev && ({ ...prev, fase: 'acompanhando', erro: '' }));
    acompanhar(track.ticket, track.loteId, 0, track.banco, operacao);
  }

  function fechar() { operacaoRef.current++; setTrack(null); }

  return { track, iniciar, abrirExistente, registrarParcela, registrarPendentes, verificarNovamente, baixarPdf, fechar };
}

export function CarneTrackingModal({ carne }: { carne: ReturnType<typeof useCarneTracking> }) {
  const { track, iniciar, registrarParcela, registrarPendentes, verificarNovamente, baixarPdf, fechar } = carne;
  const [erroLocal, setErroLocal] = useState('');

  // Limpa o erro de um download manual anterior ao trocar de carnê (ticket
  // novo, ou fechou) — sem isso, um erro de uma sessão de acompanhamento
  // ficaria visível na próxima.
  useEffect(() => {
    setErroLocal('');
  }, [track?.ticket, track?.banco]);

  return (
    <Modal
      open={!!track}
      onClose={fechar}
      title="Gerando carnê"
      subtitle={track?.total ? `${track.total} parcela(s)` : undefined}
      size="md"
    >
      {track && (
        <div className="flex flex-col gap-4 py-2">
          {track.fase === 'registrando' && (
            <div className="flex flex-col items-center gap-4 py-4 text-center">
              <Loader2 className="h-10 w-10 animate-spin text-brand-700" />
              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Registrando o carnê: {nomeBancoBoleto(track.banco)}…</p>
            </div>
          )}

          {track.fase === 'erro-registro' && (
            <div className="flex flex-col items-center gap-4 py-4 text-center">
              <AlertTriangle className="h-10 w-10 text-rose-500" />
              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Não foi possível registrar o carnê</p>
              <p className="text-xs text-rose-600 dark:text-rose-400">{track.erro}</p>
              <div className="flex gap-2">
                <Button onClick={() => iniciar(track.ids, track.banco)} className="gap-2"><RefreshCw className="h-4 w-4" />Tentar novamente</Button>
                <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={fechar}>Fechar</button>
              </div>
            </div>
          )}

          {(track.fase === 'acompanhando' || track.fase === 'aguardando-manual' || track.fase === 'completo') && (
            <>
              <div>
                <div className="mb-1.5 flex items-center justify-between text-xs">
                  <span className="font-semibold text-slate-600 dark:text-slate-300">
                    {track.prontas} de {track.total} parcela(s) confirmada(s) — {nomeBancoBoleto(track.banco)}
                  </span>
                  {track.fase === 'acompanhando' && <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-700" />}
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div
                    className={['h-full rounded-full transition-all', track.fase === 'completo' ? 'bg-emerald-500' : 'bg-brand-600'].join(' ')}
                    style={{ width: `${track.total ? Math.round((track.prontas / track.total) * 100) : 0}%` }}
                  />
                </div>
                <p className="mt-1.5 text-xs text-slate-500">
                  {track.fase === 'acompanhando'
                    ? 'Acompanhando automaticamente — isto pode levar alguns instantes.'
                    : track.fase === 'aguardando-manual'
                      ? track.banco === 'sicredi'
                        ? 'Consulte a confirmação ou retome as parcelas pendentes abaixo. Os registros incertos são consultados antes de qualquer nova emissão.'
                        : 'Parou de tentar sozinha. Gere as pendentes manualmente ou tente uma parcela específica abaixo.'
                      : 'Carnê completo.'}
                </p>
                {track.erro && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{track.erro}</p>}
              </div>

              {track.parcelas.length > 0 && (
                <div className="max-h-64 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-700">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">Parcela</th>
                        <th className="px-3 py-2 text-left font-medium">Vencimento</th>
                        <th className="px-3 py-2 text-right font-medium">Valor</th>
                        <th className="px-3 py-2 text-left font-medium">Status</th>
                        <th className="px-3 py-2 text-right font-medium">Ação</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {track.parcelas.map(p => (
                        <tr key={p.billing_id}>
                          <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{p.numero_parcela}</td>
                          <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{p.vencimento ? formatDate(p.vencimento) : '—'}</td>
                          <td className="px-3 py-2 text-right font-mono text-slate-600 dark:text-slate-300">{p.valor != null ? formatCurrency(p.valor) : '—'}</td>
                          <td className="px-3 py-2">
                            {p.status === 'registrado' && <Badge variant="success">Registrado</Badge>}
                            {p.status === 'erro' && <Badge variant="danger">Erro</Badge>}
                            {p.status === 'processando' && (
                              <Badge variant="warning">{track.fase === 'acompanhando' ? 'Processando' : track.banco === 'sicredi' ? 'Aguardando confirmação' : 'Não localizado'}</Badge>
                            )}
                            {p.erro && (p.status === 'erro' || (track.banco === 'sicredi' && p.status === 'processando')) && (
                              <p className={`mt-0.5 max-w-[16rem] text-2xs ${p.status === 'erro' ? 'truncate text-rose-500' : 'text-amber-700 dark:text-amber-400'}`} title={p.erro}>{p.erro}</p>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {p.status !== 'registrado' && track.fase !== 'acompanhando' && (
                              <button
                                type="button"
                                disabled={track.retryingId === p.billing_id}
                                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                                onClick={() => registrarParcela(p.billing_id)}
                              >
                                {track.retryingId === p.billing_id
                                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  : <RefreshCw className="h-3.5 w-3.5" />}
                                {track.banco === 'sicredi' ? 'Consultar e tentar' : 'Gerar'}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="flex flex-wrap justify-end gap-2">
                {track.fase === 'aguardando-manual' && track.ticket && track.loteId && (
                  <>
                    <Button onClick={verificarNovamente} className="gap-2"><RefreshCw className="h-4 w-4" />Verificar novamente</Button>
                    {track.prontas < track.total && (
                      <button
                        type="button"
                        disabled={track.registrandoPendentes}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200"
                        onClick={registrarPendentes}
                      >
                        {track.registrandoPendentes ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListChecks className="h-4 w-4" />}
                        {track.banco === 'sicredi' ? 'Retomar pendentes' : 'Gerar pendentes'}
                      </button>
                    )}
                    {track.prontas > 0 && (
                      <button
                        type="button"
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200"
                        onClick={async () => {
                          try { await baixarPdf(track.loteId!, track.banco, true); }
                          catch (err) { setErroLocal(parseError(err)); }
                        }}
                      ><Download className="h-4 w-4" />Baixar o que estiver pronto</button>
                    )}
                    <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={fechar}>Fechar e acompanhar depois</button>
                  </>
                )}

                {track.fase === 'completo' && (
                  <>
                    <Button
                      onClick={async () => {
                        try { await baixarPdf(track.loteId!, track.banco); setErroLocal(''); }
                        catch (err) { setErroLocal(parseError(err)); }
                      }}
                      className="gap-2"
                    ><Download className="h-4 w-4" />Baixar PDF</Button>
                    <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={fechar}>Fechar</button>
                  </>
                )}
              </div>
              {erroLocal && <p className="text-right text-xs text-rose-600 dark:text-rose-400">{erroLocal}</p>}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
