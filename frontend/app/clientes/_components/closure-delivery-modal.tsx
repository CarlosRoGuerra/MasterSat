'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Barcode,
  CalendarDays,
  Check,
  FileText,
  Inbox,
  Loader2,
  Mail,
  RefreshCw,
  Send,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatCurrency, formatDate } from '@/lib/format';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';

type Mes = {
  mes_servico: string;
  total_lotes: number;
};

type Item = {
  lote_id: number;
  billing_id: number;
  cliente: string;
  email: string | null;
  valor: number;
  vencimento: string | null;
  documentos: string[];
  boleto_emitido: boolean;
  nfse_status: string | null;
  emitir_nfse: string | null;
  estado: 'pronto' | 'bloqueado' | 'enviado' | 'processando' | 'desconhecido';
  motivo: string | null;
};

type Previa = {
  mes_servico: string;
  total_lotes: number;
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

type Filtro = 'todos' | 'pronto' | 'bloqueado' | 'enviado' | 'conferir';
const selecionavel = (item: Item) => item.estado === 'pronto' || item.estado === 'bloqueado';
const inputClass =
  'rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100';
const checkboxClass =
  'h-4 w-4 rounded accent-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:opacity-30';
const status = {
  pronto: {
    label: 'Pronto para enviar',
    color: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300',
  },
  bloqueado: {
    label: 'Pendente',
    color: 'bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300',
  },
  enviado: {
    label: 'Enviado',
    color: 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300',
  },
  processando: {
    label: 'Em envio',
    color: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  },
  desconhecido: {
    label: 'Conferir envio',
    color: 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300',
  },
};

export function ClosureDeliveryModal({
  open,
  token,
  onClose,
}: {
  open: boolean;
  token: string | null;
  onClose: () => void;
}) {
  const [meses, setMeses] = useState<Mes[]>([]);
  const [mesServico, setMesServico] = useState('');
  const [previa, setPrevia] = useState<Previa | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [loadingMeses, setLoadingMeses] = useState(false);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [quantidade, setQuantidade] = useState('25');
  const [filtro, setFiltro] = useState<Filtro>('todos');
  const operacaoRef = useRef(false);
  const requestId = useRef(0);
  const busy = sending || loading || loadingMeses;
  const itens = previa?.itens ?? [];
  const visiveis = itens.filter(
    (item) =>
      filtro === 'todos' ||
      (filtro === 'conferir'
        ? item.estado === 'processando' || item.estado === 'desconhecido'
        : item.estado === filtro),
  );
  const disponiveis = visiveis.filter(selecionavel);
  const marcados = itens.filter((item) => selecionados.has(item.billing_id));
  const prontos = marcados.filter((item) => item.estado === 'pronto');
  const todosMarcados =
    disponiveis.length > 0 && disponiveis.every((item) => selecionados.has(item.billing_id));
  const algunsMarcados = disponiveis.some((item) => selecionados.has(item.billing_id));
  const quantidadeValida = Number.isInteger(Number(quantidade)) && Number(quantidade) > 0;

  function aplicarPrevia(data: Previa) {
    setPrevia(data);
    setSelecionados(
      (current) =>
        new Set(
          data.itens
            .filter((item) => selecionavel(item) && current.has(item.billing_id))
            .map((item) => item.billing_id),
        ),
    );
  }

  function toggle(id: number) {
    setSelecionados((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function atualizarPrevia() {
    if (!token || !mesServico) return;
    const request = ++requestId.current;
    setLoading(true);
    setError('');
    try {
      const data = await apiFetch<Previa>(`/billing-closure/meses/${mesServico}`, {}, token);
      if (request === requestId.current) aplicarPrevia(data);
    } catch (err) {
      if (request === requestId.current) {
        setPrevia(null);
        setError(mensagemErro(err));
      }
    } finally {
      if (request === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (!open || !token) return;
    let active = true;
    setLoadingMeses(true);
    setError('');
    apiFetch('/billing-closure/lotes/recuperar', { method: 'POST' }, token)
      .then(() => apiFetch<Mes[]>('/billing-closure/meses', {}, token))
      .then((data) => {
        if (active) setMeses(data);
      })
      .catch((err) => {
        if (active) setError(mensagemErro(err));
      })
      .finally(() => {
        if (active) setLoadingMeses(false);
      });
    return () => {
      active = false;
    };
  }, [open, token]);

  useEffect(() => {
    setPrevia(null);
    setSelecionados(new Set());
    setFiltro('todos');
    setFeedback('');
    const request = ++requestId.current;
    if (!open || !token || !mesServico) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError('');
    setFeedback('');
    apiFetch<Previa>(`/billing-closure/meses/${mesServico}`, {}, token)
      .then((data) => {
        if (active && request === requestId.current) setPrevia(data);
      })
      .catch((err) => {
        if (active && request === requestId.current) setError(mensagemErro(err));
      })
      .finally(() => {
        if (active && request === requestId.current) setLoading(false);
      });
    return () => {
      active = false;
      requestId.current += 1;
    };
  }, [open, token, mesServico]);

  async function enviarSelecionados() {
    if (!token || !previa || busy || operacaoRef.current || !prontos.length) return;
    if (
      !window.confirm(
        `Enviar por e-mail ${prontos.length} cobrança(s) selecionada(s) dos fechamentos de ${rotuloMes(previa.mes_servico)}? ` +
          `Cada responsável receberá o boleto e, quando seu cadastro exigir, também a NFS-e.`,
      )
    )
      return;

    operacaoRef.current = true;
    setSending(true);
    setError('');
    setFeedback('');
    let enviados = 0;
    const falhas: string[] = [];
    try {
      for (const [index, item] of prontos.entries()) {
        setProgress(`${index + 1}/${prontos.length} · ${item.cliente}`);
        try {
          await apiFetch(
            `/billing-closure/lotes/${item.lote_id}/enviar/${item.billing_id}`,
            {
              method: 'POST',
            },
            token,
          );
          enviados += 1;
          setSelecionados((current) => {
            const next = new Set(current);
            next.delete(item.billing_id);
            return next;
          });
          setPrevia((current) =>
            current
              ? {
                  ...current,
                  prontos: current.prontos - 1,
                  enviados: current.enviados + 1,
                  itens: current.itens.map((row) =>
                    row.billing_id === item.billing_id
                      ? { ...row, estado: 'enviado', motivo: null }
                      : row,
                  ),
                }
              : current,
          );
        } catch (err) {
          falhas.push(`#${item.billing_id} ${item.cliente}: ${mensagemErro(err)}`);
        }
      }
      await atualizarPrevia();
      setFeedback(
        `${enviados} envio(s) concluído(s)${falhas.length ? `; ${falhas.length} não concluído(s)` : ''}.`,
      );
      if (falhas.length)
        setError(
          falhas.slice(0, 8).join(' · ') +
            (falhas.length > 8 ? ` · e mais ${falhas.length - 8}` : ''),
        );
    } catch (err) {
      setError(mensagemErro(err));
    } finally {
      operacaoRef.current = false;
      setSending(false);
      setProgress('');
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!operacaoRef.current) onClose();
      }}
      title="Enviar fechamento por e-mail"
      subtitle="Financeiro / Fechamento mensal"
      description="Confira os documentos, escolha as cobranças e envie no seu ritmo."
      size="2xl"
      footer={
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm">
            <p className="font-semibold text-slate-900 dark:text-white">
              {marcados.length} selecionada(s){' '}
              <span className="mx-2 font-normal text-slate-300">/</span>{' '}
              <span className="tabular-nums">
                {formatCurrency(marcados.reduce((total, item) => total + item.valor, 0))}
              </span>
            </p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              {marcados.length
                ? `${prontos.length} pronta(s) para envio${marcados.length > prontos.length ? ` · ${marcados.length - prontos.length} com pendências` : ''}`
                : 'Selecione as cobranças para montar seu lote.'}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={atualizarPrevia}
              disabled={busy || !mesServico}
            >
              <RefreshCw aria-hidden="true" className="h-4 w-4" />
              Atualizar conferência
            </Button>
            <Button type="button" onClick={enviarSelecionados} disabled={busy || !prontos.length}>
              <Send aria-hidden="true" className="h-4 w-4" />
              Enviar selecionadas ({prontos.length})
            </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-5">
        <div className="grid overflow-hidden rounded-xl border border-slate-200 md:grid-cols-[minmax(250px,1fr)_2fr] dark:border-slate-700">
          <div className="border-l-4 border-brand-500 bg-slate-900 p-5 text-white">
            <label
              htmlFor="closure-month"
              className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300"
            >
              <CalendarDays aria-hidden="true" className="h-4 w-4 text-brand-400" />
              Mês do serviço
            </label>
            <select
              id="closure-month"
              value={mesServico}
              disabled={busy}
              onChange={(e) => setMesServico(e.target.value)}
              className={`${inputClass} w-full`}
            >
              <option value="">Selecione o mês</option>
              {meses.map((mes) => (
                <option key={mes.mes_servico} value={mes.mes_servico}>
                  {rotuloMes(mes.mes_servico)} · {mes.total_lotes} fechamento(s)
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">
              Somente cobranças geradas no fechamento.
            </p>
          </div>
          <div className="flex flex-col justify-center gap-4 bg-slate-50/70 p-5 dark:bg-slate-800/40">
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                Conferência de documentos
              </h4>
              <span className="text-xs text-slate-500">
                {previa ? `${previa.total} cobranças no mês` : 'Boleto e nota fiscal'}
              </span>
            </div>
            <div className="grid grid-cols-3 divide-x divide-slate-200 dark:divide-slate-700">
              {[
                {
                  label: 'Prontas',
                  value: previa?.prontos,
                  color: 'text-emerald-700 dark:text-emerald-400',
                },
                {
                  label: 'Pendentes',
                  value: previa?.bloqueados,
                  color: 'text-amber-700 dark:text-amber-400',
                },
                {
                  label: 'Enviadas',
                  value: previa?.enviados,
                  color: 'text-blue-700 dark:text-blue-400',
                },
              ].map((stat, index) => (
                <div key={stat.label} className={index ? 'pl-5' : ''}>
                  <p className={`text-2xl font-semibold tabular-nums ${stat.color}`}>
                    {stat.value ?? '—'}
                  </p>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{stat.label}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200"
          >
            {error}
          </p>
        )}
        {feedback && (
          <p
            role="status"
            className="flex items-center gap-2 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
          >
            <Check aria-hidden="true" className="h-4 w-4" />
            {feedback}
          </p>
        )}
        {sending && (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Loader2
              aria-hidden="true"
              className="h-4 w-4 animate-spin motion-reduce:animate-none"
            />
            Enviando cobranças · {progress}
          </p>
        )}
        {(loading || loadingMeses) && !sending && (
          <p role="status" className="flex items-center gap-2 py-4 text-sm text-slate-500">
            <Loader2
              aria-hidden="true"
              className="h-4 w-4 animate-spin motion-reduce:animate-none"
            />
            Carregando fechamento…
          </p>
        )}
        {previa && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-1" aria-label="Filtrar cobranças">
                {(
                  [
                    { id: 'todos', label: 'Todas', count: previa.total },
                    { id: 'pronto', label: 'Prontas', count: previa.prontos },
                    { id: 'bloqueado', label: 'Pendentes', count: previa.bloqueados },
                    { id: 'enviado', label: 'Enviadas', count: previa.enviados },
                    ...(previa.indeterminados
                      ? [{ id: 'conferir', label: 'Conferir', count: previa.indeterminados }]
                      : []),
                  ] as { id: Filtro; label: string; count: number }[]
                ).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    aria-pressed={filtro === tab.id}
                    disabled={busy}
                    onClick={() => setFiltro(tab.id)}
                    className={`rounded-lg px-3 py-2 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${filtro === tab.id ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900' : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}
                  >
                    {tab.label} <span className="ml-1 tabular-nums opacity-70">{tab.count}</span>
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label
                  htmlFor="closure-quantity"
                  className="text-xs text-slate-600 dark:text-slate-300"
                >
                  Quantidade do lote
                </label>
                <input
                  id="closure-quantity"
                  type="number"
                  min="1"
                  step="1"
                  value={quantidade}
                  disabled={busy}
                  onChange={(e) => setQuantidade(e.target.value)}
                  className={`${inputClass} w-20 tabular-nums`}
                />
                <Button
                  variant="secondary"
                  disabled={busy || !quantidadeValida || !disponiveis.length}
                  onClick={() =>
                    setSelecionados(
                      new Set(
                        disponiveis.slice(0, Number(quantidade)).map((item) => item.billing_id),
                      ),
                    )
                  }
                  className="text-xs"
                >
                  Selecionar
                </Button>
                {marcados.length > 0 && (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setSelecionados(new Set())}
                    className="text-xs"
                  >
                    Limpar seleção
                  </Button>
                )}
              </div>
            </div>
            <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
              <div className="max-h-[38vh] overflow-auto">
                <table className="w-full min-w-[900px] text-left text-xs">
                  <thead className="sticky top-0 z-10 bg-slate-50 text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                    <tr>
                      <th scope="col" className="w-12 px-4 py-3">
                        <input
                          type="checkbox"
                          aria-label="Selecionar todas as cobranças disponíveis neste filtro"
                          className={checkboxClass}
                          checked={todosMarcados}
                          ref={(el) => {
                            if (el) el.indeterminate = algunsMarcados && !todosMarcados;
                          }}
                          disabled={busy || !disponiveis.length}
                          onChange={() =>
                            setSelecionados((current) => {
                              const next = new Set(current);
                              disponiveis.forEach((item) => {
                                if (todosMarcados) next.delete(item.billing_id);
                                else next.add(item.billing_id);
                              });
                              return next;
                            })
                          }
                        />
                      </th>
                      <th scope="col" className="px-3 py-3 font-medium">
                        Cobrança / responsável
                      </th>
                      <th scope="col" className="px-3 py-3 font-medium">
                        Vencimento
                      </th>
                      <th scope="col" className="px-3 py-3 text-right font-medium">
                        Valor
                      </th>
                      <th scope="col" className="px-4 py-3 font-medium">
                        Documentos
                      </th>
                      <th scope="col" className="px-3 py-3 font-medium">
                        Conferência
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {visiveis.map((item) => (
                      <tr
                        key={item.billing_id}
                        className={`align-top ${selecionados.has(item.billing_id) ? 'bg-brand-50/70 dark:bg-brand-950/20' : 'hover:bg-slate-50/70 dark:hover:bg-slate-800/40'}`}
                      >
                        <td className="px-4 py-4">
                          <input
                            type="checkbox"
                            aria-label={`Selecionar cobrança #${item.billing_id}`}
                            className={checkboxClass}
                            disabled={busy || !selecionavel(item)}
                            checked={selecionados.has(item.billing_id)}
                            onChange={() => toggle(item.billing_id)}
                          />
                        </td>
                        <td className="px-3 py-3">
                          <span className="font-mono text-2xs text-slate-400">
                            #{item.billing_id}
                          </span>
                          <p className="mt-0.5 max-w-sm font-semibold leading-5 text-slate-800 dark:text-slate-100">
                            {item.cliente}
                          </p>
                          <span className="mt-1 flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
                            <Mail aria-hidden="true" className="h-3 w-3 shrink-0" />
                            {item.email || 'Sem e-mail cadastrado'}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-4 tabular-nums text-slate-600 dark:text-slate-300">
                          {item.vencimento ? formatDate(item.vencimento) : '—'}
                        </td>
                        <td className="whitespace-nowrap px-3 py-4 text-right font-semibold tabular-nums text-slate-800 dark:text-slate-100">
                          {formatCurrency(item.valor)}
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex flex-wrap gap-1.5">
                            {item.documentos.map((documento) => {
                              const emitido =
                                documento === 'Boleto'
                                  ? item.boleto_emitido
                                  : item.nfse_status === 'emitida';
                              return (
                                <span
                                  key={documento}
                                  title={`${documento}: ${emitido ? 'emitido' : 'pendente'}`}
                                  className={`inline-flex items-center gap-1 rounded border px-2 py-1 ${emitido ? 'border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300' : 'border-dashed border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-300'}`}
                                >
                                  {documento === 'Boleto' ? (
                                    <Barcode aria-hidden="true" className="h-3 w-3" />
                                  ) : (
                                    <FileText aria-hidden="true" className="h-3 w-3" />
                                  )}
                                  {documento}
                                  {emitido && (
                                    <Check
                                      aria-hidden="true"
                                      className="h-3 w-3 text-emerald-600"
                                    />
                                  )}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td className="max-w-xs px-3 py-3">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-2xs font-semibold ${status[item.estado].color}`}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-current" />
                            {status[item.estado].label}
                          </span>
                          {item.motivo && (
                            <p className="mt-1.5 text-2xs leading-relaxed text-slate-500 dark:text-slate-400">
                              {item.motivo}
                            </p>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!visiveis.length && (
                  <p className="py-10 text-center text-sm text-slate-500">
                    Nenhuma cobrança neste filtro.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap justify-between gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2.5 text-2xs text-slate-500 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-400">
                <span>
                  {visiveis.length} de {previa.total} cobrança(s) · {previa.total_lotes}{' '}
                  fechamento(s)
                </span>
                <span>Enviadas e envios a conferir não podem ser selecionados.</span>
              </div>
            </div>
          </>
        )}
        {!previa && !loading && !loadingMeses && !error && (
          <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-200 px-5 py-10 text-center dark:border-slate-700">
            <Inbox aria-hidden="true" className="mb-3 h-8 w-8 text-slate-300" />
            <p className="text-sm font-medium text-slate-700 dark:text-slate-200">
              {meses.length
                ? 'Escolha o mês para preparar seu envio'
                : 'Nenhum fechamento encontrado'}
            </p>
            <p className="mt-1 max-w-md text-xs leading-relaxed text-slate-500">
              {meses.length
                ? 'Você poderá selecionar as cobranças e enviar os documentos por e-mail.'
                : 'Gere as cobranças de um fechamento para começar.'}
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}
