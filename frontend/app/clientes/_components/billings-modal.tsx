import type { Dispatch, SetStateAction } from 'react';
import { ChevronDown, ChevronRight, Download, DollarSign, Flag, Mail, MessageCircle, Receipt, Wrench } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { ActionBtn } from './action-btn';
import { valorComJuros } from './helpers';
import type { BillingItem, CarneItem } from './types';

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
  onSendEmail: (b: BillingItem) => void;
  onSendWhats: (b: BillingItem) => void;
  onBaixarPdf: (b: BillingItem) => void;
  onBaixarComprovante: (b: BillingItem) => void;
}) {
  const fmt = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);

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
              { label: 'Total cobrado', value: billings.reduce((s, b) => s + b.amount, 0) },
              { label: 'Total pago', value: billings.reduce((s, b) => s + (b.paid_amount ?? 0), 0) },
              { label: 'Pendente / vencido', value: billings.filter((b) => ['pendente', 'vencida'].includes(b.status)).reduce((s, b) => s + b.amount, 0) },
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

      {loading ? (
        <TableSkeleton rows={5} cols={8} />
      ) : billings.length === 0 ? (
        <EmptyState icon={DollarSign} title="Nenhuma cobrança encontrada" description="Não há boletos registrados para este cliente." />
      ) : (
        <Table>
          <TableHead>
            <Th className="w-8">
              <input
                type="checkbox"
                className="h-4 w-4 rounded accent-brand-700"
                title="Selecionar todos os boletos em aberto"
                checked={
                  billings.some((b) => b.status === 'pendente' || b.status === 'vencida') &&
                  billings.filter((b) => b.status === 'pendente' || b.status === 'vencida').every((b) => selectedIds.includes(b.id))
                }
                onChange={(e) => onSelectedIdsChange(
                  e.target.checked
                    ? billings.filter((b) => b.status === 'pendente' || b.status === 'vencida').map((b) => b.id)
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
            {billings.map((b) => {
              const isAberto = b.status === 'pendente' || b.status === 'vencida';
              const juros = valorComJuros(b);
              return (
                <Tr key={b.id}>
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
                  <Td className="text-xs text-slate-500">{b.id}</Td>
                  <Td className="text-xs capitalize">{b.billing_type === 'prorata' ? 'Pró-rata' : b.billing_type === 'recorrente' ? 'Mensalidade' : b.billing_type}</Td>
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
            })}
          </TableBody>
        </Table>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Mostrando {billings.length} registro(s)
      </p>
    </Modal>
  );
}
