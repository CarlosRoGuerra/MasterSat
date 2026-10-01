import { Fragment, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { CheckCircle2, ChevronDown, ChevronRight, Download, DollarSign, Flag, Mail, MessageCircle, Receipt, Wrench } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { ActionBtn } from './action-btn';
import { valorComJuros } from './helpers';
import type { BillingItem, CarneItem } from './types';

type Linha = { tipo: 'avulso'; billing: BillingItem } | { tipo: 'sgr'; cod: string; itens: BillingItem[] };

// No SGR o boleto é consolidado por cliente: 10 veículos = 1 boleto com 10
// linhas. A migração grava 1 cobrança por linha (cada uma no seu contrato),
// então aqui elas voltam a aparecer como o boleto único que o cliente recebeu.
function agruparPorBoletoSgr(billings: BillingItem[]): Linha[] {
  const porCod = new Map<string, BillingItem[]>();
  for (const b of billings) {
    const cod = b.sgr_payload?.cod_boleto;
    if (cod) porCod.set(cod, [...(porCod.get(cod) ?? []), b]);
  }
  const vistos = new Set<string>();
  const linhas: Linha[] = [];
  for (const b of billings) {
    const cod = b.sgr_payload?.cod_boleto;
    const grupo = cod ? porCod.get(cod) ?? [] : [];
    if (!cod || grupo.length < 2) {
      linhas.push({ tipo: 'avulso', billing: b });
    } else if (!vistos.has(cod)) {
      vistos.add(cod);
      linhas.push({ tipo: 'sgr', cod, itens: grupo });
    }
  }
  return linhas;
}

function tipoLabel(billingType: string) {
  if (billingType === 'prorata') return 'Pró-rata';
  if (billingType === 'recorrente') return 'Mensalidade';
  return billingType.replace(/_/g, ' ');
}

const emAberto = (b: BillingItem) => b.status === 'pendente' || b.status === 'vencida';

// Veículo fora do escopo da migração não vira cadastro, mas a placa segue no
// título que o importador grava: 'Boleto SGR 17678 - ABC1234'.
const placaDa = (b: BillingItem) => b.vehicle_plate ?? b.title?.match(/^Boleto SGR \S+ - (\S+)$/)?.[1] ?? null;

type Filtros = {
  situacao: string;
  mesDe: string; mesAte: string;        // 'AAAA-MM' (input type=month)
  vencDe: string; vencAte: string;      // 'AAAA-MM-DD'
  pagoDe: string; pagoAte: string;
};
const FILTROS_VAZIOS: Filtros = { situacao: '', mesDe: '', mesAte: '', vencDe: '', vencAte: '', pagoDe: '', pagoAte: '' };

const SITUACOES: Record<string, (b: BillingItem) => boolean> = {
  aberto: emAberto,
  vencida: (b) => b.status === 'vencida',
  pendente: (b) => b.status === 'pendente',
  paga: (b) => b.status === 'paga',
  cancelada: (b) => b.status === 'cancelada',
};

// period_label vem como 'MM/AAAA' (ou 'AAAA-MM'); vira 'AAAA-MM' para
// comparar com o input de mês. Rótulo fora do padrão fica de fora do filtro.
function mesRef(label?: string | null): string | null {
  if (!label) return null;
  const br = label.match(/^(\d{1,2})\/(\d{4})$/);
  if (br) return `${br[2]}-${br[1].padStart(2, '0')}`;
  const iso = label.match(/^(\d{4})-(\d{2})/);
  return iso ? `${iso[1]}-${iso[2]}` : null;
}

function dentro(valor: string | null | undefined, de: string, ate: string): boolean {
  if (!de && !ate) return true;
  if (!valor) return false;
  return (!de || valor >= de) && (!ate || valor <= ate);
}

function aplicarFiltros(billings: BillingItem[], f: Filtros): BillingItem[] {
  const situacao = f.situacao ? SITUACOES[f.situacao] : null;
  return billings.filter((b) =>
    (!situacao || situacao(b))
    && dentro(mesRef(b.period_label), f.mesDe, f.mesAte)
    && dentro(b.due_date?.slice(0, 10), f.vencDe, f.vencAte)
    && dentro(b.payment_date?.slice(0, 10), f.pagoDe, f.pagoAte));
}

export function BillingsModal({
  open,
  clientName,
  loading,
  billings,
  carnes,
  carneExpandido,
  summaryExpanded,
  selectedIds,
  gerandoCarne,
  onClose,
  onToggleSummary,
  onSelectedIdsChange,
  onToggleCarne,
  onBaixarCarne,
  onOpenUnify,
  onGerarCarne,
  onEditBilling,
  onBillingHistory,
  onReceiveBilling,
  onSendEmail,
  onSendWhats,
  onBaixarPdf,
  onBaixarComprovante,
}: {
  open: boolean;
  clientName?: string;
  loading: boolean;
  billings: BillingItem[];
  carnes: CarneItem[];
  carneExpandido: number | null;
  summaryExpanded: boolean;
  selectedIds: number[];
  gerandoCarne: boolean;
  onClose: () => void;
  onToggleSummary: () => void;
  onSelectedIdsChange: Dispatch<SetStateAction<number[]>>;
  onToggleCarne: (loteId: number) => void;
  onBaixarCarne: (loteId: number) => void;
  onOpenUnify: () => void;
  onGerarCarne: () => void;
  onEditBilling: (b: BillingItem) => void;
  onBillingHistory: (b: BillingItem) => void;
  onReceiveBilling: (b: BillingItem) => void;
  onSendEmail: (b: BillingItem) => void;
  onSendWhats: (b: BillingItem) => void;
  onBaixarPdf: (b: BillingItem) => void;
  onBaixarComprovante: (b: BillingItem) => void;
}) {
  const fmt = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
  const [boletosAbertos, setBoletosAbertos] = useState<Set<string>>(new Set());
  const [filtros, setFiltros] = useState<Filtros>(FILTROS_VAZIOS);
  useEffect(() => { setFiltros(FILTROS_VAZIOS); setBoletosAbertos(new Set()); }, [clientName]);
  const filtrando = Object.values(filtros).some(Boolean);
  // Parcela mais recente no topo (vencimento desc; empate pelo id mais novo).
  const visiveis = aplicarFiltros(billings, filtros)
    .sort((a, b) => (b.due_date ?? '').localeCompare(a.due_date ?? '') || b.id - a.id);
  const linhas = agruparPorBoletoSgr(visiveis);
  const campo = (k: keyof Filtros) => ({
    value: filtros[k],
    onChange: (e: { target: { value: string } }) => setFiltros((f) => ({ ...f, [k]: e.target.value })),
  });

  function toggleBoleto(cod: string) {
    setBoletosAbertos((prev) => {
      const next = new Set(prev);
      if (next.has(cod)) next.delete(cod); else next.add(cod);
      return next;
    });
  }

  function linhaCobranca(b: BillingItem, dentroDeBoleto = false) {
    const isAberto = emAberto(b);
    const juros = valorComJuros(b);
    return (
      <Tr key={b.id} className={dentroDeBoleto ? 'bg-slate-50/70 dark:bg-slate-900/40' : undefined}>
        <Td>
          {isAberto ? (
            <input
              type="checkbox"
              className="h-4 w-4 rounded accent-brand-700"
              checked={selectedIds.includes(b.id)}
              onChange={() => onSelectedIdsChange((prev) =>
                prev.includes(b.id) ? prev.filter((id) => id !== b.id) : [...prev, b.id]
              )}
            />
          ) : null}
        </Td>
        <Td className={`text-xs text-slate-500 ${dentroDeBoleto ? 'pl-8' : ''}`}>{b.id}</Td>
        <Td className="text-xs">
          <span className="capitalize">{tipoLabel(b.billing_type)}</span>
          {placaDa(b) && <span className="block font-mono text-2xs text-slate-500">{placaDa(b)}</span>}
        </Td>
        <Td className="text-xs">{b.created_at ? new Date(b.created_at).toLocaleDateString('pt-BR') : '—'}</Td>
        <Td className="text-sm font-medium">{b.due_date}</Td>
        <Td className="text-xs">{b.payment_date ?? '—'}</Td>
        <Td className="font-mono font-semibold">{fmt(b.amount)}</Td>
        <Td className="font-mono font-semibold text-rose-600 dark:text-rose-400">
          {juros != null ? fmt(juros) : '—'}
        </Td>
        <Td className="font-mono text-emerald-700 dark:text-emerald-400">{fmt(b.paid_amount ?? 0)}</Td>
        <Td className="text-xs text-center">
          {b.installment_number ? `${b.installment_number}/${b.installment_total}` : '1/1'}
        </Td>
        <Td className="text-xs">{b.period_label ?? '—'}</Td>
        <Td><Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge></Td>
        <Td>
          <div className="flex justify-end gap-1">
            <ActionBtn color="purple" icon={Wrench} title="Alterar boleto" onClick={() => onEditBilling(b)} />
            <ActionBtn color="purple" icon={Flag} title="Histórico de operações" onClick={() => onBillingHistory(b)} />
            {isAberto && (
              <>
                <ActionBtn color="yellow" icon={CheckCircle2} title="Marcar como pago (baixa manual)" onClick={() => onReceiveBilling(b)} />
                <ActionBtn color="blue" icon={Mail} title="Enviar boleto por e-mail" onClick={() => onSendEmail(b)} />
                <ActionBtn color="green" icon={MessageCircle} title="Enviar boleto via Whats" onClick={() => onSendWhats(b)} />
                {b.boleto_ailos && (
                  <ActionBtn color="teal" icon={Download} title="Baixar boleto PDF" onClick={() => onBaixarPdf(b)} />
                )}
              </>
            )}
            {b.status === 'paga' && (
              <ActionBtn color="blue" icon={Receipt} title="Emitir comprovante de pagamento" onClick={() => onBaixarComprovante(b)} />
            )}
          </div>
        </Td>
      </Tr>
    );
  }

  function linhaBoletoSgr(cod: string, itens: BillingItem[]) {
    const aberto = boletosAbertos.has(cod);
    const primeiro = itens[0];
    const abertos = itens.filter(emAberto);
    const todosSelecionados = abertos.length > 0 && abertos.every((b) => selectedIds.includes(b.id));
    const juros = itens.map((b) => valorComJuros(b));
    const totalJuros = juros.some((j) => j != null)
      ? itens.reduce((s, b, i) => s + (juros[i] ?? b.amount), 0)
      : null;
    const status = Array.from(new Set(itens.map((b) => b.status)));
    const periodos = Array.from(new Set(itens.map((b) => b.period_label).filter(Boolean)));
    const placas = new Set(itens.map(placaDa).filter(Boolean)).size;
    return (
      <Fragment key={`sgr-${cod}`}>
        <Tr className="cursor-pointer" onClick={() => toggleBoleto(cod)}>
          <Td onClick={(e) => e.stopPropagation()}>
            {abertos.length > 0 ? (
              <input
                type="checkbox"
                className="h-4 w-4 rounded accent-brand-700"
                title="Selecionar todas as linhas em aberto deste boleto"
                checked={todosSelecionados}
                onChange={() => onSelectedIdsChange((prev) => {
                  const ids = abertos.map((b) => b.id);
                  return todosSelecionados
                    ? prev.filter((id) => !ids.includes(id))
                    : Array.from(new Set([...prev, ...ids]));
                })}
              />
            ) : null}
          </Td>
          <Td className="text-xs text-slate-500">
            <span className="flex items-center gap-1">
              {aberto ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              {cod}
            </span>
          </Td>
          <Td className="text-xs">
            <span className="font-semibold">Boleto SGR</span>
            <span className="block text-2xs text-slate-500">
              {itens.length} itens{placas ? ` · ${placas} placa(s)` : ''}
            </span>
          </Td>
          <Td className="text-xs">{primeiro.created_at ? new Date(primeiro.created_at).toLocaleDateString('pt-BR') : '—'}</Td>
          <Td className="text-sm font-medium">{primeiro.due_date}</Td>
          <Td className="text-xs">{primeiro.payment_date ?? '—'}</Td>
          <Td className="font-mono font-semibold">{fmt(itens.reduce((s, b) => s + b.amount, 0))}</Td>
          <Td className="font-mono font-semibold text-rose-600 dark:text-rose-400">
            {totalJuros != null ? fmt(totalJuros) : '—'}
          </Td>
          <Td className="font-mono text-emerald-700 dark:text-emerald-400">
            {fmt(itens.reduce((s, b) => s + (b.paid_amount ?? 0), 0))}
          </Td>
          <Td className="text-xs text-center">
            {primeiro.installment_number ? `${primeiro.installment_number}/${primeiro.installment_total}` : '1/1'}
          </Td>
          <Td className="text-xs">{periodos.join(', ') || '—'}</Td>
          <Td>
            <div className="flex flex-wrap gap-1">
              {status.map((s) => <Badge key={s} variant={statusVariant(s)}>{statusLabel(s)}</Badge>)}
            </div>
          </Td>
          <Td className="text-right text-2xs text-slate-500">{aberto ? 'Ocultar itens' : 'Ver itens'}</Td>
        </Tr>
        {aberto && itens.map((b) => linhaCobranca(b, true))}
      </Fragment>
    );
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={clientName ? `Boletos do cliente — ${clientName}` : 'Boletos do cliente'}
      size="2xl"
    >
      {/* Resumo financeiro */}
      <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50">
        <div className="flex items-center justify-between px-4 py-3">
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">Resumo financeiro</p>
          <Button type="button" variant="secondary" onClick={onToggleSummary} className="px-4 py-1.5 text-xs">
            {summaryExpanded ? 'Ocultar' : 'Exibir'}
          </Button>
        </div>
        {summaryExpanded && !loading && (
          <div className="grid gap-3 px-4 pb-4 sm:grid-cols-3">
            {[
              { label: filtrando ? 'Total cobrado (filtro)' : 'Total cobrado', value: visiveis.reduce((s, b) => s + b.amount, 0) },
              { label: filtrando ? 'Total pago (filtro)' : 'Total pago', value: visiveis.reduce((s, b) => s + (b.paid_amount ?? 0), 0) },
              { label: filtrando ? 'Pendente / vencido (filtro)' : 'Pendente / vencido', value: visiveis.filter(emAberto).reduce((s, b) => s + b.amount, 0) },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-lg border border-slate-200 bg-white p-3 text-center dark:border-slate-700 dark:bg-slate-800">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-1 text-base font-bold text-slate-900 dark:text-white">
                  {new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value)}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Soma dos boletos selecionados (pagamento em lote) */}
      {selectedIds.length > 0 && (() => {
        const sel = billings.filter((b) => selectedIds.includes(b.id));
        const total = sel.reduce((s, b) => s + b.amount, 0);
        const totalJuros = sel.reduce((s, b) => s + (valorComJuros(b) ?? b.amount), 0);
        return (
          <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-brand-300 bg-brand-50 px-4 py-3 text-sm dark:border-brand-700 dark:bg-brand-950/30">
            <span className="font-bold text-brand-800 dark:text-brand-200">
              {sel.length} boleto(s) selecionado(s)
            </span>
            <span className="text-slate-600 dark:text-slate-300">
              Total sem juros: <strong className="font-mono text-slate-900 dark:text-white">{fmt(total)}</strong>
            </span>
            <span className="text-slate-600 dark:text-slate-300">
              Total com juros: <strong className="font-mono text-rose-600 dark:text-rose-400">{fmt(totalJuros)}</strong>
            </span>
            {sel.length >= 2 && (
              <Button onClick={onOpenUnify} className="!py-1.5 text-xs">
                Unificar em 1 boleto
              </Button>
            )}
            {sel.length >= 2 && (
              <Button variant="secondary" onClick={onGerarCarne} disabled={gerandoCarne} className="!py-1.5 text-xs">
                {gerandoCarne ? 'Gerando carnê…' : 'Gerar carnê'}
              </Button>
            )}
            <button
              type="button"
              onClick={() => onSelectedIdsChange([])}
              className="ml-auto text-xs text-slate-500 underline hover:text-slate-600 dark:hover:text-slate-200"
            >
              Limpar seleção
            </button>
          </div>
        );
      })()}

      {/* Carnês já gerados deste cliente — reabrir/baixar */}
      {carnes.length > 0 && (
        <div className="mb-4 rounded-xl border border-slate-200 dark:border-slate-700">
          <p className="border-b border-slate-100 px-4 py-2 text-xs font-semibold text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Carnês gerados
          </p>
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {carnes.map((c) => {
              const prontas = c.parcelas_registradas >= c.parcelas;
              const quitado = c.parcelas_pagas >= c.parcelas;
              const aberto = carneExpandido === c.lote_id;
              return (
                <div key={c.lote_id}>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-sm">
                    <button
                      type="button"
                      onClick={() => onToggleCarne(c.lote_id)}
                      className="flex items-center gap-1.5 font-semibold text-slate-700 hover:underline dark:text-slate-200"
                    >
                      {aberto ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      Carnê #{c.lote_id}
                    </button>
                    <span className="text-slate-500 dark:text-slate-400">{c.parcelas} parcela(s) · {fmt(c.total)}</span>
                    {c.criado_em && <span className="text-xs text-slate-500">{new Date(c.criado_em).toLocaleDateString('pt-BR')}</span>}
                    <Badge variant={quitado ? 'success' : c.parcelas_pagas > 0 ? 'info' : 'default'}>
                      {c.parcelas_pagas}/{c.parcelas} paga(s)
                    </Badge>
                    {!prontas && (
                      <Badge variant="warning">{c.parcelas_registradas}/{c.parcelas} registrada(s) na Ailos</Badge>
                    )}
                    <Button variant="secondary" onClick={() => onBaixarCarne(c.lote_id)} className="ml-auto !py-1.5 text-xs">
                      Baixar carnê
                    </Button>
                  </div>
                  {aberto && (
                    <div className="border-t border-slate-100 bg-slate-50/60 px-4 py-2 dark:border-slate-800 dark:bg-slate-900/40">
                      <table className="w-full text-xs">
                        <thead className="text-slate-500">
                          <tr>
                            <th className="py-1 text-left font-medium">Parcela</th>
                            <th className="py-1 text-left font-medium">Vencimento</th>
                            <th className="py-1 text-right font-medium">Valor</th>
                            <th className="py-1 text-left font-medium">Situação</th>
                            <th className="py-1 text-left font-medium">Pago em</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                          {c.parcelas_detalhe.map((p) => (
                            <tr key={p.billing_id}>
                              <td className="py-1 text-slate-600 dark:text-slate-300">{p.numero_parcela ?? '—'}</td>
                              <td className="py-1 text-slate-500 dark:text-slate-400">{p.vencimento ? new Date(p.vencimento).toLocaleDateString('pt-BR') : '—'}</td>
                              <td className="py-1 text-right font-mono text-slate-600 dark:text-slate-300">{fmt(p.valor)}</td>
                              <td className="py-1"><Badge variant={statusVariant(p.status)}>{statusLabel(p.status)}</Badge></td>
                              <td className="py-1 text-slate-500 dark:text-slate-400">{p.data_pagamento ? new Date(p.data_pagamento).toLocaleDateString('pt-BR') : '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!loading && billings.length > 0 && (
        <div className="mb-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400">
              Situação
              <Select className="mt-1" {...campo('situacao')}>
                <option value="">Todas</option>
                <option value="aberto">Em aberto (pendentes + vencidas)</option>
                <option value="vencida">Vencidas (em atraso)</option>
                <option value="pendente">A vencer</option>
                <option value="paga">Pagas</option>
                <option value="cancelada">Canceladas</option>
              </Select>
            </label>
            <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
              Mês referente
              <div className="mt-1 flex items-center gap-1.5">
                <Input type="month" aria-label="Mês referente de" {...campo('mesDe')} />
                <span>a</span>
                <Input type="month" aria-label="Mês referente até" {...campo('mesAte')} />
              </div>
            </div>
            <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
              Vencimento
              <div className="mt-1 flex items-center gap-1.5">
                <Input type="date" aria-label="Vencimento de" {...campo('vencDe')} />
                <span>a</span>
                <Input type="date" aria-label="Vencimento até" {...campo('vencAte')} />
              </div>
            </div>
            <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
              Pagamento
              <div className="mt-1 flex items-center gap-1.5">
                <Input type="date" aria-label="Pagamento de" {...campo('pagoDe')} />
                <span>a</span>
                <Input type="date" aria-label="Pagamento até" {...campo('pagoAte')} />
              </div>
            </div>
          </div>
          {filtrando && (
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                onClick={() => setFiltros(FILTROS_VAZIOS)}
                className="text-xs text-slate-500 underline hover:text-slate-700 dark:hover:text-slate-200"
              >
                Limpar filtros
              </button>
            </div>
          )}
        </div>
      )}

      {loading ? (
        <TableSkeleton rows={5} cols={8} />
      ) : billings.length === 0 ? (
        <EmptyState icon={DollarSign} title="Nenhuma cobrança encontrada" description="Não há boletos registrados para este cliente." />
      ) : visiveis.length === 0 ? (
        <EmptyState icon={DollarSign} title="Nenhuma cobrança neste filtro" description="Ajuste ou limpe os filtros acima." />
      ) : (
        <Table>
          <TableHead>
            <Th className="w-8">
              <input
                type="checkbox"
                className="h-4 w-4 rounded accent-brand-700"
                title="Selecionar todos os boletos em aberto"
                checked={
                  visiveis.some(emAberto) &&
                  visiveis.filter(emAberto).every((b) => selectedIds.includes(b.id))
                }
                onChange={(e) => onSelectedIdsChange(
                  e.target.checked
                    ? visiveis.filter(emAberto).map((b) => b.id)
                    : []
                )}
              />
            </Th>
            <Th>Nº</Th>
            <Th>Tipo</Th>
            <Th>Emissão</Th>
            <Th>Vencimento</Th>
            <Th>Pagamento</Th>
            <Th>Valor</Th>
            <Th>Valor c/ Juros</Th>
            <Th>Valor Pago</Th>
            <Th>Parcela</Th>
            <Th>Mês Ref.</Th>
            <Th>Situação</Th>
            <Th className="w-44" />
          </TableHead>
          <TableBody>
            {linhas.map((l) => (l.tipo === 'sgr' ? linhaBoletoSgr(l.cod, l.itens) : linhaCobranca(l.billing)))}
          </TableBody>
        </Table>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Mostrando {linhas.length} boleto(s)
        {linhas.length !== visiveis.length && ` · ${visiveis.length} cobrança(s) — boletos do SGR com várias placas aparecem agrupados`}
        {filtrando && ` · filtro ativo (${visiveis.length} de ${billings.length} cobranças)`}
      </p>
    </Modal>
  );
}
