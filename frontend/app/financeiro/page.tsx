'use client';

import { FormEvent, Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { TrendingUp, AlertTriangle, FileText, CheckCircle2, Clock, MoreHorizontal, ChevronDown, ChevronRight, Lock, PenSquare, ListChecks, Banknote, Layers, Mail, PieChart, Barcode, Wallet, Coins, FilePlus, Tags, BookText, Eye } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { BarChart } from '@/components/ui/bar-chart';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { ErrorBanner } from '@/components/ui/error-banner';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { CarneTrackingModal, useCarneTracking } from '@/components/carne-tracking-modal';
import { API_URL, apiFetch } from '@/lib/api';
import { entregarArquivo, nomeArquivoCliente } from '@/lib/arquivo';
import { enviarBoletoEmail, enviarBoletoWhats } from '@/lib/boleto-mensagem';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import type { BillingStatus, ClientOption, VehicleOption, TrackerOption } from '@/lib/domain-types';

/* ── Types ──────────────────────────────────────────────────────────── */
const TAB_ORDER = ['menu', 'overview', 'management', 'payables'] as const;
type FinanceiroTab = (typeof TAB_ORDER)[number];

type BillingInterval = 1 | 3 | 6 | 12;

type Payable = { id: number; description: string; supplier?: string | null; category?: string | null; amount: number; due_date: string; status: string; payment_date?: string | null; payment_method?: string | null; notes?: string | null; overdue_days: number };
type Plan = { id: number; name: string; price: number; description?: string | null; active: boolean; billing_interval_months: BillingInterval; default_installation_fee?: number | null; default_uninstall_fee?: number | null; default_billing_day?: number | null; default_duration_months?: number | null; active_contracts: number };
type ServiceProduct = { id: number; name: string; category: string; default_price: number; description?: string | null; active: boolean; allow_installments: boolean; remove_after_payment: boolean; auto_add_on_uninstall: boolean };
type Contract = { id: number; client_id: number; plan_id: number; vehicle_id?: number | null; tracker_id?: number | null; start_date: string; end_date?: string | null; status: string; billing_day?: number | null; payment_method?: string | null; notes?: string | null; installation_fee?: number | null; uninstall_fee?: number | null; signed?: boolean | null; signed_at?: string | null; client_name?: string | null; plan_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; monthly_value?: number | null; open_billings: number; next_due_date?: string | null };
type ChargeItem = { id: number; client_id: number; contract_id?: number | null; vehicle_id?: number | null; tracker_id?: number | null; service_product_id?: number | null; title: string; description?: string | null; quantity: number; unit_price: number; total_amount: number; installment_count: number; start_date: string; active: boolean; remove_after_payment: boolean; completed_at?: string | null; status: string; client_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; service_product_name?: string | null; open_installments: number };
type Billing = { id: number; contract_id?: number | null; client_id: number; payer_client_id?: number | null; item_id?: number | null; vehicle_id?: number | null; tracker_id?: number | null; title?: string | null; billing_type: string; installment_number?: number | null; installment_total?: number | null; amount: number; due_date: string; status: BillingStatus; payment_date?: string | null; payment_method?: string | null; notes?: string | null; paid_amount?: number | null; receipt_number?: string | null; period_label?: string | null; client_name?: string | null; payer_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; plan_name?: string | null; contract_status?: string | null; overdue_days: number };
type Summary = { active_plans: number; active_contracts: number; pending_billings: number; overdue_billings: number; pending_amount: number; overdue_amount: number; paid_this_month: number };
type RevenueItem = { label: string; total_received: number; total_billed: number; total_outstanding: number };
type DelinquentItem = { client_id: number; client_name: string; total_open: number; overdue_count: number };
type Nfse = { billing_id: number; status: string; numero_nfse?: string | null; serie_nfse?: string | null; codigo_verificacao?: string | null; chave_acesso?: string | null; link_visualizacao?: string | null; protocolo?: string | null; situacao?: string | null; erro_codigo?: string | null; erro_mensagem?: string | null };

type PlanFormState = { name: string; price: string; description: string; active: boolean; billing_interval_months: string; default_installation_fee: string; default_uninstall_fee: string; default_billing_day: string; default_duration_months: string };
type ServiceProductFormState = { name: string; category: string; default_price: string; description: string; active: boolean; allow_installments: boolean; remove_after_payment: boolean; auto_add_on_uninstall: boolean };
type ContractFormState = { client_id: string; vehicle_id: string; tracker_id: string; plan_id: string; start_date: string; end_date: string; billing_day: string; payment_method: string; notes: string; installation_fee: string; uninstall_fee: string; signed: boolean; signed_at: string };
type ChargeItemFormState = { client_id: string; contract_id: string; vehicle_id: string; tracker_id: string; service_product_id: string; title: string; description: string; quantity: string; unit_price: string; installment_count: string; start_date: string; remove_after_payment: boolean };
type ReceiveFormState = { paid_amount: string; payment_date: string; payment_method: string; notes: string };
type AdjustFormState = { amount: string; due_date: string; justification: string };

/* ── Constants ──────────────────────────────────────────────────────── */
const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';
const areaClass = `${fieldClass} min-h-[88px] resize-y`;
const initialPlanForm: PlanFormState = { name: '', price: '', description: '', active: true, billing_interval_months: '1', default_installation_fee: '', default_uninstall_fee: '', default_billing_day: '', default_duration_months: '' };
const initialProductForm: ServiceProductFormState = { name: '', category: 'servico', default_price: '', description: '', active: true, allow_installments: true, remove_after_payment: false, auto_add_on_uninstall: false };
const initialContractForm: ContractFormState = { client_id: '', vehicle_id: '', tracker_id: '', plan_id: '', start_date: new Date().toISOString().slice(0, 10), end_date: '', billing_day: '', payment_method: 'boleto', notes: '', installation_fee: '', uninstall_fee: '', signed: false, signed_at: '' };
const initialChargeItemForm: ChargeItemFormState = { client_id: '', contract_id: '', vehicle_id: '', tracker_id: '', service_product_id: '', title: '', description: '', quantity: '1', unit_price: '', installment_count: '1', start_date: new Date().toISOString().slice(0, 10), remove_after_payment: false };
const initialReceiveForm: ReceiveFormState = { paid_amount: '', payment_date: new Date().toISOString().slice(0, 10), payment_method: 'pix', notes: '' };
const initialAdjustForm: AdjustFormState = { amount: '', due_date: '', justification: '' };

/* ── Helpers ────────────────────────────────────────────────────────── */
function parseError(error: unknown) { return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.'; }
function formatCurrency(value: number) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value || 0); }
function intervalLabel(months: number) { return ({ 1: 'Mensal', 3: 'Trimestral', 6: 'Semestral', 12: 'Anual' } as Record<number, string>)[months] || `${months} meses`; }

/** Data (YYYY-MM-DD) somada de N meses — para a vigência do contrato a partir do início. */
function addMonthsISO(startISO: string, months: number): string {
  if (!startISO) return '';
  const d = new Date(startISO + 'T12:00:00');
  d.setMonth(d.getMonth() + months);
  return d.toISOString().slice(0, 10);
}
function formatDate(iso?: string | null) {
  if (!iso) return '—';
  // accepts "2026-05-22" or full ISO strings
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
  return d.toLocaleDateString('pt-BR');
}

async function downloadProtectedFile(path: string, token: string, filename: string): Promise<void> {
  const url = `${API_URL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
  let response: Response;
  try {
    response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new Error('Não foi possível conectar ao servidor. Verifique se o backend está em execução.');
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { const data = await response.json(); detail = data?.detail || detail; } catch { try { detail = await response.text() || detail; } catch { /* noop */ } }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

/* ── RowMenu: three-dot dropdown for table actions ───────────────────
   Replaces inline red "Excluir" buttons — destructive action is hidden
   behind an explicit extra click, reducing accidental deletions.
─────────────────────────────────────────────────────────────────────── */
function RowMenu({
  onEdit,
  onDelete,
  onPdf,
  disabled,
}: {
  onEdit: () => void;
  onDelete: () => void;
  onPdf?: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label="Opções"
        disabled={disabled}
        onClick={() => setOpen(v => !v)}
        className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 min-w-[120px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
          <button
            type="button"
            onClick={() => { setOpen(false); onEdit(); }}
            className="flex w-full items-center px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Editar
          </button>
          {onPdf && (
            <button
              type="button"
              onClick={() => { setOpen(false); onPdf(); }}
              className="flex w-full items-center px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Contrato (PDF)
            </button>
          )}
          <button
            type="button"
            onClick={() => { setOpen(false); onDelete(); }}
            className="flex w-full items-center px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30"
          >
            Excluir
          </button>
        </div>
      )}
    </div>
  );
}

/* ── GroupedContractsTable ──────────────────────────────────────────── */
function GroupedContractsTable({
  contracts,
  delinquents,
  canEdit,
  statusFilter,
  onEdit,
  onDelete,
  onPdf,
}: {
  contracts: Contract[];
  delinquents: DelinquentItem[];
  canEdit: boolean;
  statusFilter: string;
  onEdit: (c: Contract) => void;
  onDelete: (c: Contract) => void;
  onPdf: (c: Contract) => void;
}) {
  const delinquentIds = useMemo(() => new Set(delinquents.map(d => d.client_id)), [delinquents]);

  const groups = useMemo(() => {
    const map = new Map<number, { clientId: number; clientName: string; items: Contract[] }>();
    for (const c of contracts) {
      if (!map.has(c.client_id)) map.set(c.client_id, { clientId: c.client_id, clientName: c.client_name ?? '—', items: [] });
      map.get(c.client_id)!.items.push(c);
    }
    return Array.from(map.values()).sort((a, b) => a.clientName.localeCompare(b.clientName, 'pt-BR'));
  }, [contracts]);

  // Delinquent clients start expanded
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set(delinquents.map(d => d.client_id)));

  useEffect(() => {
    setExpanded(prev => {
      const next = new Set(prev);
      delinquents.forEach(d => next.add(d.client_id));
      return next;
    });
  }, [delinquents]);

  function toggle(id: number) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const visible = useMemo(() => {
    if (!statusFilter) return groups;
    return groups
      .map(g => ({ ...g, items: g.items.filter(c => c.status === statusFilter) }))
      .filter(g => g.items.length > 0);
  }, [groups, statusFilter]);

  if (visible.length === 0) {
    return <EmptyState title="Nenhum contrato" description="Nenhum contrato encontrado com os filtros selecionados." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-800">
            <th className="py-2.5 pl-4 pr-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Cliente / Vínculo</th>
            <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Plano</th>
            <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Próx. venc.</th>
            <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
            <th className="w-12 py-2.5 pr-4" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {visible.map(group => {
            const isDelinquent = delinquentIds.has(group.clientId);
            const isExpanded = expanded.has(group.clientId);
            return (
              <Fragment key={group.clientId}>
                {/* Group header row */}
                <tr
                  className={[
                    'cursor-pointer transition-colors',
                    isDelinquent
                      ? 'bg-red-50/60 hover:bg-red-50 dark:bg-red-950/20 dark:hover:bg-red-950/30'
                      : 'bg-slate-50 hover:bg-slate-100 dark:bg-slate-900/40 dark:hover:bg-slate-800/60',
                  ].join(' ')}
                  onClick={() => toggle(group.clientId)}
                >
                  <td className="py-3 pl-4 pr-3" colSpan={5}>
                    <div className="flex items-center gap-2">
                      {isExpanded
                        ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                        : <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />}
                      <span className="max-w-[320px] truncate font-semibold text-slate-800 dark:text-slate-100">{group.clientName}</span>
                      <span className="shrink-0 text-xs text-slate-500">
                        ({group.items.length} contrato{group.items.length !== 1 ? 's' : ''})
                      </span>
                      {isDelinquent && (
                        <Badge variant="danger" className="ml-1 text-[10px]">inadimplente</Badge>
                      )}
                    </div>
                  </td>
                </tr>

                {/* Contract sub-rows */}
                {isExpanded && group.items.map(c => (
                  <tr
                    key={c.id}
                    className="hover:bg-slate-50/80 dark:hover:bg-slate-900/40"
                  >
                    <td className="py-2.5 pl-10 pr-3">
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {c.vehicle_plate || c.tracker_identifier || 'Contrato geral'}
                      </p>
                    </td>
                    <td className="px-3 py-2.5 text-sm text-slate-700 dark:text-slate-300">{c.plan_name ?? '—'}</td>
                    <td className="px-3 py-2.5 text-xs text-slate-500">{formatDate(c.next_due_date)}</td>
                    <td className="px-3 py-2.5">
                      <Badge variant={statusVariant(c.status)}>{statusLabel(c.status)}</Badge>
                    </td>
                    <td className="py-2.5 pr-4">
                      {canEdit && (
                        <div className="flex justify-end">
                          <RowMenu onEdit={() => onEdit(c)} onDelete={() => onDelete(c)} onPdf={() => onPdf(c)} />
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── BillingTableSection ────────────────────────────────────────────── */
function BillingTableSection({
  billings,
  loading,
  error,
  billingView,
  billingSearch,
  billingStatusFilter,
  onViewToggle,
  onSearchChange,
  onStatusFilterChange,
  onRefresh,
  onSelect,
  selectedId,
  onNewBilling,
  batchIds,
  onBatchIdsChange,
  onBatchReceive,
  onBatchCancel,
  onBatchMaint,
  batchActions,
  rowActionLabel,
}: {
  billings: Billing[];
  loading: boolean;
  error?: string;
  billingView: 'alert' | 'all';
  billingSearch: string;
  billingStatusFilter: string;
  onViewToggle: () => void;
  onSearchChange: (v: string) => void;
  onStatusFilterChange: (v: string) => void;
  onRefresh: () => void;
  onSelect: (b: Billing) => void;
  selectedId?: number;
  onNewBilling?: () => void;
  batchIds?: number[];
  onBatchIdsChange?: (ids: number[]) => void;
  onBatchReceive?: () => void;
  onBatchCancel?: () => void;
  onBatchMaint?: () => void;
  batchActions?: Array<'receive' | 'cancel' | 'maint'>;
  rowActionLabel?: string;
}) {
  const acoesLote = batchActions ?? ['receive', 'cancel', 'maint'];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const in7 = new Date(today); in7.setDate(in7.getDate() + 7);

  const list = billingView === 'alert'
    ? billings
        .filter(b => b.status === 'vencida' || (b.status === 'pendente' && new Date(b.due_date) <= in7))
        .sort((a, b) => b.overdue_days - a.overdue_days || new Date(a.due_date).getTime() - new Date(b.due_date).getTime())
    : billings;

  const pg = usePagination(list, 25);

  const fc = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

  return (
    <Card id="billing-section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeader eyebrow="Cobranças" title={billingView === 'alert' ? 'Atenção imediata' : 'Carteira completa'} />
        <div className="flex gap-2">
          {onNewBilling && (
            <Button onClick={onNewBilling} className="text-xs px-3 py-1.5">
              Nova cobrança avulsa
            </Button>
          )}
          <Button variant="secondary" onClick={onViewToggle} className="text-xs px-3 py-1.5">
            {billingView === 'alert' ? `Ver todas (${billings.length})` : 'Ver urgentes'}
          </Button>
          <Button variant="secondary" onClick={onRefresh} disabled={loading} className="text-xs px-3 py-1.5">
            {loading ? 'Atualizando…' : 'Atualizar'}
          </Button>
        </div>
      </div>
      {billingView === 'all' && (
        <div className="mt-3 flex flex-wrap gap-3">
          <input className={fc} style={{ maxWidth: 260 }} placeholder="Buscar por número, cliente ou título" value={billingSearch} onChange={e => onSearchChange(e.target.value)} />
          <select className={fc} style={{ width: 160 }} value={billingStatusFilter} onChange={e => onStatusFilterChange(e.target.value)}>
            <option value="">Todos os status</option>
            <option value="pendente">Pendente</option>
            <option value="paga">Paga</option>
            <option value="vencida">Vencida</option>
            <option value="cancelada">Cancelada</option>
          </select>
        </div>
      )}
      {/* Barra de ações em lote */}
      {batchIds && batchIds.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-brand-300 bg-brand-50 px-4 py-2.5 text-sm dark:border-brand-700 dark:bg-brand-950/30">
          <span className="font-bold text-brand-800 dark:text-brand-200">{batchIds.length} selecionada(s)</span>
          {onBatchReceive && acoesLote.includes('receive') && <Button onClick={onBatchReceive} className="!py-1.5 text-xs">Receber em lote</Button>}
          {onBatchMaint && acoesLote.includes('maint') && <Button variant="secondary" onClick={onBatchMaint} className="!py-1.5 text-xs">Alterar venc./valor</Button>}
          {onBatchCancel && acoesLote.includes('cancel') && <Button variant="secondary" onClick={onBatchCancel} className="!py-1.5 text-xs">Cancelar em lote</Button>}
          <button type="button" onClick={() => onBatchIdsChange?.([])} className="ml-auto text-xs text-slate-400 underline hover:text-slate-600 dark:hover:text-slate-200">
            Limpar seleção
          </button>
        </div>
      )}

      <div className="mt-4">
        {loading ? <TableSkeleton rows={8} cols={5} /> : error ? (
          // "Todas em dia" seria uma mentira tranquilizadora se a carga falhou.
          <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar as cobranças" description="Veja o erro acima e tente novamente." />
        ) : list.length === 0 ? (
          <EmptyState icon={CheckCircle2} title={billingView === 'alert' ? 'Nenhuma cobrança urgente' : 'Nenhuma cobrança encontrada'} description={billingView === 'alert' ? 'Todas as cobranças estão em dia.' : 'Tente ajustar os filtros.'} />
        ) : (
          <>
            <Table>
              <TableHead>
                {onBatchIdsChange && (
                  <Th className="w-8">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded accent-brand-700"
                      title="Selecionar todas em aberto (desta página)"
                      checked={
                        pg.slice.some(b => b.status === 'pendente' || b.status === 'vencida') &&
                        pg.slice.filter(b => b.status === 'pendente' || b.status === 'vencida').every(b => (batchIds ?? []).includes(b.id))
                      }
                      onChange={e => onBatchIdsChange(
                        e.target.checked
                          ? Array.from(new Set([...(batchIds ?? []), ...pg.slice.filter(b => b.status === 'pendente' || b.status === 'vencida').map(b => b.id)]))
                          : []
                      )}
                    />
                  </Th>
                )}
                <Th>Responsável financeiro</Th>
                <Th>Título</Th>
                <Th>Vencimento</Th>
                <Th>Valor</Th>
                <Th>Status</Th>
                <Th className="w-24" />
              </TableHead>
              <TableBody>
                {pg.slice.map(b => (
                  <Tr key={b.id} selected={b.id === selectedId}>
                    {onBatchIdsChange && (
                      <Td>
                        {(b.status === 'pendente' || b.status === 'vencida') ? (
                          <input
                            type="checkbox"
                            className="h-4 w-4 rounded accent-brand-700"
                            checked={(batchIds ?? []).includes(b.id)}
                            onChange={() => {
                              const cur = batchIds ?? [];
                              onBatchIdsChange(cur.includes(b.id) ? cur.filter(id => id !== b.id) : [...cur, b.id]);
                            }}
                          />
                        ) : null}
                      </Td>
                    )}
                    <Td>
                      <p className="font-medium">{b.payer_name ?? b.client_name ?? '—'}</p>
                      <p className="text-xs text-slate-400">
                        {b.payer_name && b.client_name && b.payer_name !== b.client_name
                          ? `Cliente atendido: ${b.client_name}${b.vehicle_plate ? ` • ${b.vehicle_plate}` : ''}`
                          : (b.vehicle_plate ?? '')}
                      </p>
                    </Td>
                    <Td className="text-xs text-slate-500">{b.title ?? b.plan_name ?? b.billing_type}</Td>
                    <Td>
                      <p className="text-sm">{formatDate(b.due_date)}</p>
                      {b.overdue_days > 0 && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{b.overdue_days}d atraso</p>}
                    </Td>
                    <Td className="font-mono font-semibold">{formatCurrency(b.amount)}</Td>
                    <Td><Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge></Td>
                    <Td>
                      <Button variant="secondary" onClick={() => onSelect(b)} className="px-3 py-1.5 text-xs">
                        {rowActionLabel && (b.status === 'pendente' || b.status === 'vencida') ? rowActionLabel : 'Detalhes'}
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
            <Pagination {...pg} onPage={pg.setPage} className="mt-2" />
          </>
        )}
      </div>
    </Card>
  );
}

/* ── Page ────────────────────────────────────────────────────────────── */
export default function FinanceiroPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/financeiro'], '/login/admin');
  const canEdit = !!user && (user.role === 'admin' || user.role === 'financeiro');

  const [plans, setPlans] = useState<Plan[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [trackers, setTrackers] = useState<TrackerOption[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [serviceProducts, setServiceProducts] = useState<ServiceProduct[]>([]);
  const [chargeItems, setChargeItems] = useState<ChargeItem[]>([]);
  const [billings, setBillings] = useState<Billing[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [revenue, setRevenue] = useState<RevenueItem[]>([]);
  const [delinquents, setDelinquents] = useState<DelinquentItem[]>([]);
  const [selectedBilling, setSelectedBilling] = useState<Billing | null>(null);
  const [nfse, setNfse] = useState<Nfse | null>(null);
  const [nfseLoading, setNfseLoading] = useState(false);
  const [boletoLoading, setBoletoLoading] = useState(false);
  const [editingPlanId, setEditingPlanId] = useState<number | null>(null);
  const [editingProductId, setEditingProductId] = useState<number | null>(null);
  const [editingContractId, setEditingContractId] = useState<number | null>(null);
  const [planForm, setPlanForm] = useState<PlanFormState>(initialPlanForm);
  const [serviceProductForm, setServiceProductForm] = useState<ServiceProductFormState>(initialProductForm);
  const [contractForm, setContractForm] = useState<ContractFormState>(initialContractForm);
  const [chargeItemForm, setChargeItemForm] = useState<ChargeItemFormState>(initialChargeItemForm);
  const [receiveForm, setReceiveForm] = useState<ReceiveFormState>(initialReceiveForm);
  const [adjustForm, setAdjustForm] = useState<AdjustFormState>(initialAdjustForm);
  const [billingSearch, setBillingSearch] = useState('');
  const [billingStatusFilter, setBillingStatusFilter] = useState('');
  const [billingView, setBillingView] = useState<'alert' | 'all'>('alert');
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState(false);

  // Nova cobrança avulsa (instalação, serviço pontual etc. — sem esperar o fechamento)
  const [newBillingModal, setNewBillingModal] = useState(false);
  const [newBillingForm, setNewBillingForm] = useState({ client_id: '', title: '', amount: '', due_date: '', notes: '' });

  // Saúde da sessão Ailos (emissão de boletos depende dela)
  const [ailosStatus, setAilosStatus] = useState<{ cooperado_status: string } | null>(null);
  const [connectingAilos, setConnectingAilos] = useState(false);

  // Contas a Pagar
  const [payables, setPayables] = useState<Payable[]>([]);
  const [payableStatusFilter, setPayableStatusFilter] = useState('pendente');
  const [payableModal, setPayableModal] = useState(false);
  const [payableForm, setPayableForm] = useState({ description: '', supplier: '', category: '', amount: '', due_date: '', notes: '' });
  const [payingPayable, setPayingPayable] = useState<Payable | null>(null);
  const [payPayableForm, setPayPayableForm] = useState({ payment_date: new Date().toISOString().slice(0, 10), payment_method: 'pix' });

  // Modais do menu (tudo dentro do Financeiro, sem trocar de tela).
  // O modo define o comportamento da carteira: linha vai direto para a ação
  // do card clicado e o lote mostra só os botões correspondentes.
  type CarteiraMode = 'full' | 'situacao' | 'situacao-lote' | 'manutencao' | 'manutencao-lote';
  const [carteiraMode, setCarteiraMode] = useState<CarteiraMode | null>(null);
  const [envioModal, setEnvioModal] = useState(false);
  const [envioClientId, setEnvioClientId] = useState('');
  const [envioCliente, setEnvioCliente] = useState<{ id: number; name: string; phone?: string | null; email?: string | null } | null>(null);
  const [envioBillings, setEnvioBillings] = useState<Billing[]>([]);
  const [envioLoading, setEnvioLoading] = useState(false);

  // Operações em lote na carteira de cobranças
  const [selectedBillingIds, setSelectedBillingIds] = useState<number[]>([]);
  const [gerandoCarne, setGerandoCarne] = useState(false);
  // Modal "Gerar carnê" (por cliente)
  const [carneModal, setCarneModal] = useState(false);
  const [carneClientId, setCarneClientId] = useState('');
  const [carneBillings, setCarneBillings] = useState<Billing[]>([]);
  const [carneSelected, setCarneSelected] = useState<number[]>([]);
  const [carneLoading, setCarneLoading] = useState(false);
  const [carneMode, setCarneMode] = useState<'existentes' | 'plano'>('existentes');
  const [carneContractId, setCarneContractId] = useState('');
  const [carneNumParcelas, setCarneNumParcelas] = useState('12');
  const [carnePrimeiroVenc, setCarnePrimeiroVenc] = useState('');
  const [carneValor, setCarneValor] = useState('');
  type CarneGerado = {
    lote_id: number;
    ticket: string | null;
    criado_em: string | null;
    parcelas: number;
    parcelas_registradas: number;
    total: number;
    status: string;
  };
  const [carnesGerados, setCarnesGerados] = useState<CarneGerado[]>([]);
  const [batchReceiveModal, setBatchReceiveModal] = useState(false);
  const [batchReceiveForm, setBatchReceiveForm] = useState({ payment_date: new Date().toISOString().slice(0, 10), payment_method: 'pix' });
  const [batchMaintModal, setBatchMaintModal] = useState(false);
  const [batchMaintForm, setBatchMaintForm] = useState({ due_date: '', amount: '', justification: '' });
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [planModal, setPlanModal] = useState(false);
  const [productModal, setProductModal] = useState(false);
  const [contractModal, setContractModal] = useState(false);
  const [chargeModal, setChargeModal] = useState(false);
  const [receiveModal, setReceiveModal] = useState(false);
  const [adjustModal, setAdjustModal] = useState(false);

  // Tab navigation with URL sync
  const [activeTab, setActiveTab] = useState<FinanceiroTab>('menu');

  // Plans table sort
  const [planSort, setPlanSort] = useState<{ field: 'name' | 'price' | 'active_contracts'; dir: 'asc' | 'desc' }>({ field: 'name', dir: 'asc' });

  function switchTab(tab: FinanceiroTab) {
    setActiveTab(tab);
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.set('tab', tab);
    window.history.pushState({}, '', url.toString());
  }

  // Read tab from URL on mount
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const tab = params.get('tab');
    if (tab === 'overview' || tab === 'management' || tab === 'payables') setActiveTab(tab);
  }, []);

  // Clique na linha da carteira: vai direto para a ação do card que abriu o
  // modal (alterar situação → registrar pagamento; manutenção → ajustar).
  function carteiraRowSelect(b: Billing) {
    const aberta = b.status === 'pendente' || b.status === 'vencida';
    setSelectedBilling(b);
    if (carteiraMode === 'situacao' && aberta) setReceiveModal(true);
    if (carteiraMode === 'manutencao' && aberta) setAdjustModal(true);
  }

  // Seleção de cliente no modal "Enviar boletos" — carrega contato + abertas
  async function selecionarClienteEnvio(id: string) {
    setEnvioClientId(id);
    setEnvioCliente(null);
    setEnvioBillings([]);
    if (!token || !id) return;
    setEnvioLoading(true);
    try {
      const [cli, bills] = await Promise.all([
        apiFetch<{ id: number; name: string; phone?: string | null; email?: string | null }>(`/clients/${id}`, {}, token),
        apiFetch<Billing[]>(`/billings?client_id=${id}&limit=100`, {}, token),
      ]);
      setEnvioCliente(cli);
      setEnvioBillings(bills.filter(b => b.status === 'pendente' || b.status === 'vencida'));
    } catch (err) { setError(parseError(err)); }
    finally { setEnvioLoading(false); }
  }

  function openCreatePlan() { setEditingPlanId(null); setPlanForm(initialPlanForm); setModalError(''); setPlanModal(true); }
  function openCreateProduct() { setEditingProductId(null); setServiceProductForm(initialProductForm); setModalError(''); setProductModal(true); }
  function openCreateContract() { setEditingContractId(null); setContractForm(initialContractForm); setModalError(''); setContractModal(true); }

  function openEditPlan(plan: Plan) {
    setEditingPlanId(plan.id);
    setPlanForm({ name: plan.name, price: String(plan.price), description: plan.description || '', active: plan.active, billing_interval_months: String(plan.billing_interval_months), default_installation_fee: plan.default_installation_fee != null ? String(plan.default_installation_fee) : '', default_uninstall_fee: plan.default_uninstall_fee != null ? String(plan.default_uninstall_fee) : '', default_billing_day: plan.default_billing_day != null ? String(plan.default_billing_day) : '', default_duration_months: plan.default_duration_months != null ? String(plan.default_duration_months) : '' });
    setModalError('');
    setPlanModal(true);
  }

  function openEditProduct(product: ServiceProduct) {
    setEditingProductId(product.id);
    setServiceProductForm({ name: product.name, category: product.category, default_price: String(product.default_price), description: product.description || '', active: product.active, allow_installments: product.allow_installments, remove_after_payment: product.remove_after_payment, auto_add_on_uninstall: product.auto_add_on_uninstall });
    setModalError('');
    setProductModal(true);
  }

  async function handleDeletePlan(plan: Plan) {
    if (!token || !canEdit) return;
    if (!window.confirm(`Deseja remover o plano "${plan.name}"? Esta ação não pode ser desfeita.`)) return;
    setProcessing(true);
    try {
      await apiFetch(`/plans/${plan.id}`, { method: 'DELETE' }, token);
      setFeedback('Plano removido com sucesso.');
      if (editingPlanId === plan.id) { setEditingPlanId(null); setPlanForm(initialPlanForm); setPlanModal(false); }
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleDeleteProduct(product: ServiceProduct) {
    if (!token || !canEdit) return;
    if (!window.confirm(`Deseja remover o item "${product.name}"? Esta ação não pode ser desfeita.`)) return;
    setProcessing(true);
    try {
      await apiFetch(`/service-products/${product.id}`, { method: 'DELETE' }, token);
      setFeedback('Serviço/produto removido com sucesso.');
      if (editingProductId === product.id) { setEditingProductId(null); setServiceProductForm(initialProductForm); setProductModal(false); }
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function loadData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (billingSearch) query.set('search', billingSearch);
      if (billingStatusFilter) query.set('status', billingStatusFilter);
      query.set('limit', '300');
      const [plansRes, clientsRes, vehiclesRes, trackersRes, contractsRes, productsRes, chargeItemsRes, billingsRes, summaryRes, revenueRes, delinquentRes] = await Promise.all([
        apiFetch<Plan[]>('/plans', {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=300', {}, currentToken),
        apiFetch<VehicleOption[]>('/vehicles?limit=300', {}, currentToken),
        apiFetch<TrackerOption[]>('/trackers?limit=300', {}, currentToken),
        apiFetch<Contract[]>('/contracts', {}, currentToken),
        apiFetch<ServiceProduct[]>('/service-products', {}, currentToken),
        apiFetch<ChargeItem[]>('/client-charge-items', {}, currentToken),
        apiFetch<Billing[]>(`/billings?${query.toString()}`, {}, currentToken),
        apiFetch<Summary>('/billings/summary', {}, currentToken),
        apiFetch<RevenueItem[]>('/billings/reports/revenue?period=monthly', {}, currentToken),
        apiFetch<DelinquentItem[]>('/billings/reports/delinquent', {}, currentToken),
      ]);
      setPlans(plansRes);
      setClients(clientsRes);
      setVehicles(vehiclesRes);
      setTrackers(trackersRes);
      setContracts(contractsRes);
      setServiceProducts(productsRes);
      setChargeItems(chargeItemsRes);
      setBillings(billingsRes);
      setSummary(summaryRes);
      setRevenue(revenueRes);
      setDelinquents(delinquentRes);
      if (selectedBilling) setSelectedBilling(billingsRes.find(b => b.id === selectedBilling.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  /** Recarrega só a lista de cobranças. O loadData busca 11 endpoints — usar
   *  ele na busca dinâmica recarregaria planos, clientes, gráficos e KPIs a
   *  cada tecla. */
  async function loadBillingsOnly(currentToken: string) {
    setLoading(true);
    try {
      const query = new URLSearchParams();
      if (billingSearch) query.set('search', billingSearch);
      if (billingStatusFilter) query.set('status', billingStatusFilter);
      query.set('limit', '300');
      const billingsRes = await apiFetch<Billing[]>(`/billings?${query.toString()}`, {}, currentToken);
      setBillings(billingsRes);
      if (selectedBilling) setSelectedBilling(billingsRes.find(b => b.id === selectedBilling.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadData(token);
    // Saúde da sessão Ailos — se caiu, o banner de reconexão aparece
    apiFetch<{ cooperado_status: string }>('/ailos/status', {}, token)
      .then(setAilosStatus)
      .catch(() => setAilosStatus(null));
  }, [token]);

  // O banner de erro fica no topo da página; em listas longas (planos,
  // cobranças) a mensagem aparecia fora da tela e a ação parecia falhar em
  // silêncio. Ao surgir um erro, sobe até ele.
  useEffect(() => {
    if (error) window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [error]);

  // Busca/filtro dinâmicos das cobranças
  const billingSearchDebounced = useDebouncedValue(billingSearch);
  useEffectSkipFirst(() => {
    if (token) loadBillingsOnly(token);
  }, [billingSearchDebounced, billingStatusFilter]);

  useEffect(() => {
    if (token && activeTab === 'payables') loadPayables(token);
  }, [token, activeTab]);

  useEffect(() => {
    if (selectedBilling) {
      setReceiveForm({ paid_amount: String(selectedBilling.amount || ''), payment_date: new Date().toISOString().slice(0, 10), payment_method: selectedBilling.payment_method || 'pix', notes: '' });
      setAdjustForm({ amount: String(selectedBilling.amount || ''), due_date: selectedBilling.due_date || '', justification: '' });
      // Carrega o status da NFS-e desta cobrança (404 = ainda não emitida)
      setNfse(null);
      if (token) {
        apiFetch<Nfse>(`/nfse/${selectedBilling.id}`, {}, token).then(setNfse).catch(() => setNfse(null));
      }
    }
  }, [selectedBilling, token]);

  /* ── Derived data ── */
  const visibleContracts = useMemo(() => chargeItemForm.client_id ? contracts.filter(c => c.client_id === Number(chargeItemForm.client_id)) : contracts, [contracts, chargeItemForm.client_id]);
  const chargeVehicles = useMemo(() => chargeItemForm.client_id ? vehicles.filter(v => v.client_id === Number(chargeItemForm.client_id)) : vehicles, [vehicles, chargeItemForm.client_id]);
  const chargeTrackers = useMemo(() => trackers.filter(t => (!chargeItemForm.client_id || t.client_id === Number(chargeItemForm.client_id)) && (!chargeItemForm.vehicle_id || t.vehicle_id === Number(chargeItemForm.vehicle_id) || t.vehicle_id == null)), [trackers, chargeItemForm.client_id, chargeItemForm.vehicle_id]);

  // Revenue variation: compare last two months
  const monthlyVariation = useMemo(() => {
    if (revenue.length < 2) return null;
    const last = revenue[revenue.length - 1]?.total_received ?? 0;
    const prev = revenue[revenue.length - 2]?.total_received ?? 0;
    if (prev === 0) return null;
    return ((last - prev) / prev) * 100;
  }, [revenue]);

  // Sorted plans
  const sortedPlans = useMemo(() => {
    return [...plans].sort((a, b) => {
      const dir = planSort.dir === 'asc' ? 1 : -1;
      if (planSort.field === 'name') return a.name.localeCompare(b.name, 'pt-BR') * dir;
      if (planSort.field === 'price') return (a.price - b.price) * dir;
      if (planSort.field === 'active_contracts') return (a.active_contracts - b.active_contracts) * dir;
      return 0;
    });
  }, [plans, planSort]);

  function togglePlanSort(field: typeof planSort.field) {
    setPlanSort(prev => prev.field === field ? { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { field, dir: 'asc' });
  }

  function SortIndicator({ field }: { field: typeof planSort.field }) {
    if (planSort.field !== field) return <span className="ml-0.5 text-slate-300">↕</span>;
    return <span className="ml-0.5">{planSort.dir === 'asc' ? '↑' : '↓'}</span>;
  }

  /* ── Form submit handlers (unchanged) ── */
  async function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      const num = (v: string) => v.trim() ? Number(v.replace(',', '.')) : null;
      const payload = { name: planForm.name.trim(), price: Number(planForm.price.replace(',', '.')), description: planForm.description.trim() || null, active: planForm.active, billing_interval_months: Number(planForm.billing_interval_months), default_installation_fee: num(planForm.default_installation_fee), default_uninstall_fee: num(planForm.default_uninstall_fee), default_billing_day: planForm.default_billing_day ? Number(planForm.default_billing_day) : null, default_duration_months: planForm.default_duration_months ? Number(planForm.default_duration_months) : null };
      if (editingPlanId) {
        await apiFetch(`/plans/${editingPlanId}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Plano atualizado com sucesso.');
      } else {
        await apiFetch('/plans', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Plano cadastrado com sucesso.');
      }
      setPlanForm(initialPlanForm); setEditingPlanId(null); setPlanModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      const payload = { name: serviceProductForm.name.trim(), category: serviceProductForm.category, default_price: Number(serviceProductForm.default_price.replace(',', '.')), description: serviceProductForm.description.trim() || null, active: serviceProductForm.active, allow_installments: serviceProductForm.allow_installments, remove_after_payment: serviceProductForm.remove_after_payment, auto_add_on_uninstall: serviceProductForm.auto_add_on_uninstall };
      if (editingProductId) {
        await apiFetch(`/service-products/${editingProductId}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Serviço/produto atualizado com sucesso.');
      } else {
        await apiFetch('/service-products', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Serviço/produto cadastrado com sucesso.');
      }
      setServiceProductForm(initialProductForm); setEditingProductId(null); setProductModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitContract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      // Só gera o PDF em branco (plano, vigência e taxas). Não salva contrato
      // nem preenche dados do cliente — quem preenche e assina é o cliente.
      const payload = {
        plan_id: Number(contractForm.plan_id),
        start_date: contractForm.start_date || null,
        end_date: contractForm.end_date || null,
        installation_fee: contractForm.installation_fee ? Number(contractForm.installation_fee.replace(',', '.')) : null,
        uninstall_fee: contractForm.uninstall_fee ? Number(contractForm.uninstall_fee.replace(',', '.')) : null,
      };
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/contracts/generate-pdf`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      const url = URL.createObjectURL(await resp.blob());
      const a = document.createElement('a');
      a.href = url; a.download = 'contrato-modelo.pdf';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setContractForm(initialContractForm); setContractModal(false);
      setFeedback('Contrato gerado — baixe o PDF e envie ao cliente para preencher e assinar.');
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitChargeItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch('/client-charge-items', { method: 'POST', body: JSON.stringify({ client_id: Number(chargeItemForm.client_id), contract_id: chargeItemForm.contract_id ? Number(chargeItemForm.contract_id) : null, vehicle_id: chargeItemForm.vehicle_id ? Number(chargeItemForm.vehicle_id) : null, tracker_id: chargeItemForm.tracker_id ? Number(chargeItemForm.tracker_id) : null, service_product_id: chargeItemForm.service_product_id ? Number(chargeItemForm.service_product_id) : null, title: chargeItemForm.title.trim(), description: chargeItemForm.description.trim() || null, quantity: Number(chargeItemForm.quantity || '1'), unit_price: Number(chargeItemForm.unit_price.replace(',', '.')), installment_count: Number(chargeItemForm.installment_count || '1'), start_date: chargeItemForm.start_date, remove_after_payment: chargeItemForm.remove_after_payment }) }, token);
      setFeedback('Lançamento criado com sucesso.');
      setChargeItemForm(initialChargeItemForm); setChargeModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleReceive() {
    if (!token || !selectedBilling || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch(`/billings/${selectedBilling.id}/receive`, { method: 'POST', body: JSON.stringify({ paid_amount: Number(receiveForm.paid_amount.replace(',', '.')), payment_date: receiveForm.payment_date, payment_method: receiveForm.payment_method, notes: receiveForm.notes || null }) }, token);
      setFeedback('Pagamento registrado com sucesso.'); setReceiveModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleAdjust() {
    if (!token || !selectedBilling || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch(`/billings/${selectedBilling.id}`, { method: 'PUT', body: JSON.stringify({ amount: Number(adjustForm.amount.replace(',', '.')), due_date: adjustForm.due_date, justification: adjustForm.justification.trim() }) }, token);
      setFeedback('Cobrança ajustada com sucesso.'); setAdjustModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function loadPayables(t: string) {
    try {
      setPayables(await apiFetch<Payable[]>('/payables?limit=500', {}, t));
    } catch (err) { setError(parseError(err)); }
  }

  async function handleCreatePayable() {
    if (!token || !canEdit) return;
    setModalError('');
    if (!payableForm.description.trim() || !payableForm.amount || !payableForm.due_date) {
      setModalError('Descrição, valor e vencimento são obrigatórios.');
      return;
    }
    setProcessing(true);
    try {
      await apiFetch('/payables/', {
        method: 'POST',
        body: JSON.stringify({
          description: payableForm.description.trim(),
          supplier: payableForm.supplier.trim() || null,
          category: payableForm.category.trim() || null,
          amount: Number(payableForm.amount.replace(',', '.')),
          due_date: payableForm.due_date,
          notes: payableForm.notes.trim() || null,
        }),
      }, token);
      setPayableModal(false);
      setPayableForm({ description: '', supplier: '', category: '', amount: '', due_date: '', notes: '' });
      setFeedback('Conta cadastrada.');
      await loadPayables(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handlePayPayable() {
    if (!token || !payingPayable) return;
    setProcessing(true);
    try {
      await apiFetch(`/payables/${payingPayable.id}/pay`, {
        method: 'POST', body: JSON.stringify(payPayableForm),
      }, token);
      setPayingPayable(null);
      setFeedback('Conta marcada como paga.');
      await loadPayables(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleCancelPayable(p: Payable) {
    if (!token || !window.confirm(`Cancelar a conta "${p.description}"?`)) return;
    try {
      await apiFetch(`/payables/${p.id}/cancel`, { method: 'POST' }, token);
      await loadPayables(token);
    } catch (err) { setError(parseError(err)); }
  }

  async function handleDeletePayable(p: Payable) {
    if (!token || !window.confirm(`Excluir a conta "${p.description}"?`)) return;
    try {
      await apiFetch(`/payables/${p.id}`, { method: 'DELETE' }, token);
      await loadPayables(token);
    } catch (err) { setError(parseError(err)); }
  }

  async function handleBatchReceive() {
    if (!token || selectedBillingIds.length === 0) return;
    setProcessing(true);
    try {
      const r = await apiFetch<{ processados: number[]; ignorados: number[] }>('/billings/lote/situacao', {
        method: 'POST',
        body: JSON.stringify({ billing_ids: selectedBillingIds, action: 'receber', ...batchReceiveForm }),
      }, token);
      setBatchReceiveModal(false);
      setSelectedBillingIds([]);
      setFeedback(`${r.processados.length} cobrança(s) recebida(s)${r.ignorados.length ? ` · ${r.ignorados.length} ignorada(s) (não estavam em aberto)` : ''}.`);
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleBatchCancel() {
    if (!token || selectedBillingIds.length === 0) return;
    const reason = window.prompt(`Justificativa para cancelar ${selectedBillingIds.length} cobrança(s):`, '');
    if (!reason) return;
    setProcessing(true);
    try {
      const r = await apiFetch<{ processados: number[]; ignorados: number[]; boletos_ativos?: { billing_id: number; nosso_numero: string | null }[] }>('/billings/lote/situacao', {
        method: 'POST',
        body: JSON.stringify({ billing_ids: selectedBillingIds, action: 'cancelar', reason }),
      }, token);
      setSelectedBillingIds([]);
      setFeedback(`${r.processados.length} cobrança(s) cancelada(s)${r.ignorados.length ? ` · ${r.ignorados.length} ignorada(s)` : ''}.`);
      await loadData(token);
      // Cancelamento não baixa o título na Ailos — avisa para baixa manual.
      const ativos = r.boletos_ativos ?? [];
      if (ativos.length > 0) {
        const nums = ativos.map(x => x.nosso_numero || `#${x.billing_id}`).join(', ');
        window.alert(
          `Atenção: ${ativos.length} boleto(s) cancelado(s) continuam registrados na Ailos e ainda podem ser pagos. ` +
          `O convênio não tem baixa automática — dê baixa manualmente na Ailos. Nossos números: ${nums}.`,
        );
      }
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleBatchMaint() {
    if (!token || selectedBillingIds.length === 0) return;
    if (!batchMaintForm.justification.trim()) { setModalError('Justificativa é obrigatória.'); return; }
    if (!batchMaintForm.due_date && !batchMaintForm.amount) { setModalError('Informe novo vencimento e/ou valor.'); return; }
    setProcessing(true);
    try {
      const r = await apiFetch<{ processados: number[]; ignorados: number[] }>('/billings/lote/manutencao', {
        method: 'POST',
        body: JSON.stringify({
          billing_ids: selectedBillingIds,
          due_date: batchMaintForm.due_date || null,
          amount: batchMaintForm.amount ? Number(batchMaintForm.amount.replace(',', '.')) : null,
          justification: batchMaintForm.justification.trim(),
        }),
      }, token);
      setBatchMaintModal(false);
      setBatchMaintForm({ due_date: '', amount: '', justification: '' });
      setSelectedBillingIds([]);
      setFeedback(`${r.processados.length} título(s) atualizado(s)${r.ignorados.length ? ` · ${r.ignorados.length} ignorado(s)` : ''}.`);
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  function openCarneModal() {
    setCarneMode('existentes');
    setCarneClientId('');
    setCarneBillings([]);
    setCarneSelected([]);
    setCarneContractId('');
    setCarneNumParcelas('12');
    setCarnePrimeiroVenc('');
    setCarneValor('');
    setCarnesGerados([]);
    setModalError('');
    setCarneModal(true);
  }

  // Ao escolher o cliente, lista os boletos dele que cabem num carnê: pendentes
  // e vencidos (os já pagos/cancelados ficam de fora). Pré-seleciona todos.
  // Também lista os carnês já gerados dele, para reabrir o acompanhamento.
  async function carregarBoletosCarne(clientId: string) {
    setCarneClientId(clientId);
    setCarneBillings([]);
    setCarneSelected([]);
    setCarneContractId('');
    setCarneValor('');
    setCarnesGerados([]);
    if (!token || !clientId) return;
    setCarneLoading(true);
    try {
      const [data, gerados] = await Promise.all([
        apiFetch<Billing[]>(`/billings?client_id=${clientId}&limit=200`, {}, token),
        apiFetch<CarneGerado[]>(`/boletos/carne?client_id=${clientId}`, {}, token).catch(() => []),
      ]);
      const abertos = data.filter(b => b.status === 'pendente' || b.status === 'vencida');
      setCarneBillings(abertos);
      setCarneSelected(abertos.map(b => b.id));
      setCarnesGerados(gerados);
    } catch (err) { setModalError(parseError(err)); } finally { setCarneLoading(false); }
  }

  // Tela de acompanhamento do registro do carnê na Ailos (progresso ao vivo,
  // tabela por parcela, retry individual/em massa) — lógica compartilhada em
  // components/carne-tracking-modal.tsx (também usada no cadastro de veículo).
  const carne = useCarneTracking(token, ({ total }) => {
    setFeedback(`Carnê gerado com ${total} parcela(s).`);
    loadData(token!);
  });

  // Modo "boletos existentes": carnê a partir dos boletos em aberto selecionados.
  async function gerarCarne(ids: number[]) {
    if (!token) return;
    if (ids.length < 2) { alert('Selecione ao menos 2 boletos para gerar o carnê.'); return; }
    if (!confirm(`Gerar o carnê registra ${ids.length} boletos reais na Ailos (um por parcela). Continuar?`)) return;
    setCarneModal(false);
    await carne.iniciar(ids);
  }

  // Modo "a partir do plano": cria N parcelas do contrato (valor do plano) e as
  // transforma em carnê. Vincula ao plano do veículo + a quantidade de parcelas.
  async function gerarCarneDoPlano() {
    if (!token || !carneContractId) { alert('Selecione o contrato (plano do veículo).'); return; }
    const n = Number(carneNumParcelas);
    if (!n || n < 2) { alert('Informe ao menos 2 parcelas.'); return; }
    if (!confirm(`Serão criadas ${n} parcelas do plano e registradas na Ailos como carnê. Continuar?`)) return;
    setGerandoCarne(true); setModalError('');
    try {
      const criados = await apiFetch<{ id: number }[]>('/billings/parcelar', {
        method: 'POST',
        body: JSON.stringify({
          contract_id: Number(carneContractId),
          num_parcelas: n,
          valor_parcela: carneValor ? Number(carneValor.replace(',', '.')) : null,
          primeiro_vencimento: carnePrimeiroVenc || null,
        }),
      }, token);
      setCarneModal(false);
      await carne.iniciar(criados.map(b => b.id));
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setGerandoCarne(false);
    }
  }

  async function refreshAilosStatus() {
    if (!token) return;
    try {
      const st = await apiFetch<{ cooperado_status: string }>('/ailos/status', {}, token);
      setAilosStatus(st);
      if (st.cooperado_status === 'authorized') setFeedback('Sessão Ailos conectada — emissão de boletos normalizada.');
    } catch { setAilosStatus(null); }
  }

  async function handleReconnectAilos() {
    if (!token) return;
    setConnectingAilos(true);
    try {
      const resp = await apiFetch<{ login_url: string }>('/ailos/connect', { method: 'POST' }, token);
      window.open(resp.login_url, '_blank');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setConnectingAilos(false);
    }
  }

  async function handleCreateAvulsa() {
    if (!token || !canEdit) return;
    setModalError('');
    if (!newBillingForm.client_id || !newBillingForm.amount || !newBillingForm.due_date) {
      setModalError('Cliente, valor e vencimento são obrigatórios.');
      return;
    }
    setProcessing(true);
    try {
      const nova = await apiFetch<Billing>('/billings/', {
        method: 'POST',
        body: JSON.stringify({
          client_id: Number(newBillingForm.client_id),
          title: newBillingForm.title.trim() || 'COBRANÇA AVULSA',
          billing_type: 'avulsa',
          amount: Number(newBillingForm.amount.replace(',', '.')),
          due_date: newBillingForm.due_date,
          notes: newBillingForm.notes.trim() || null,
        }),
      }, token);
      setNewBillingModal(false);
      setNewBillingForm({ client_id: '', title: '', amount: '', due_date: '', notes: '' });
      setFeedback(`Cobrança avulsa #${nova.id} criada. Gere o boleto (Ailos) e a NFS-e pelo detalhe.`);
      await loadData(token);
      setSelectedBilling(nova);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleCancel() {
    if (!token || !selectedBilling || !canEdit) return;
    const reason = window.prompt('Informe a justificativa para cancelamento:', '');
    if (!reason) return;
    setProcessing(true);
    try {
      const cancelar = (confirmar_boleto_ailos: boolean) => apiFetch(
        `/billings/${selectedBilling.id}/cancel`,
        { method: 'POST', body: JSON.stringify({ reason, confirmar_boleto_ailos }) },
        token,
      );
      try {
        await cancelar(false);
      } catch (err) {
        // Há boleto registrado na Ailos: avisa e só prossegue se confirmado.
        const e = err as Error & { status?: number; detail?: any };
        if (e.status === 409 && e.detail?.code === 'boleto_ailos_registrado') {
          if (!window.confirm(e.detail.message)) { setProcessing(false); return; }
          await cancelar(true);
        } else {
          throw err;
        }
      }
      setFeedback('Cobrança cancelada com sucesso.');
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleEmitirNfse() {
    if (!token || !selectedBilling || !canEdit) return;
    setNfseLoading(true);
    setError('');
    try {
      const result = await apiFetch<Nfse>(`/nfse/emitir/${selectedBilling.id}`, { method: 'POST' }, token);
      setNfse(result);
      setFeedback(
        result.status === 'emitida'
          ? `NFS-e ${result.numero_nfse} emitida com sucesso.`
          : result.status === 'erro'
            ? `Falha ao emitir NFS-e: ${result.erro_mensagem ?? 'erro desconhecido'}`
            : `NFS-e em processamento (protocolo ${result.protocolo}).`,
      );
    } catch (err) { setError(parseError(err)); } finally { setNfseLoading(false); }
  }

  // Abre o PDF da nota (DANFSe gerado por nós a partir do XML) em nova aba, em
  // vez de mandar o operador para a consulta pública do governo.
  async function abrirNotaPdf(billingId: number) {
    if (!token) return;
    try {
      const resp = await fetch(
        `${API_URL.replace(/\/+$/, '')}/nfse/${billingId}/danfse-local`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      entregarArquivo(await resp.blob(), `nfse-${billingId}.pdf`, { emNovaAba: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao abrir a nota fiscal');
    }
  }

  async function handleConsultarNfse() {
    if (!token || !selectedBilling) return;
    setNfseLoading(true);
    try {
      const result = await apiFetch<Nfse>(`/nfse/consultar/${selectedBilling.id}`, { method: 'POST' }, token);
      setNfse(result);
    } catch (err) { setError(parseError(err)); } finally { setNfseLoading(false); }
  }

  // Registra o boleto na Ailos (linha digitável/QR oficiais) e baixa o PDF já
  // com os dados registrados. Idempotente no backend: re-clicar não duplica.
  async function handleGerarBoleto() {
    if (!token || !selectedBilling || !canEdit) return;
    setBoletoLoading(true);
    setError('');
    try {
      const boleto = await apiFetch<{ linha_digitavel?: string | null; status_ailos?: string | null }>(
        '/ailos/boletos',
        { method: 'POST', body: JSON.stringify({ billing_id: selectedBilling.id }) },
        token,
      );
      await downloadProtectedFile(`/boletos/${selectedBilling.id}/pdf`, token, `${nomeArquivoCliente(selectedBilling.payer_name ?? selectedBilling.client_name, selectedBilling.due_date)}.pdf`);
      setFeedback(
        boleto.linha_digitavel
          ? `Boleto registrado na Ailos. Linha digitável: ${boleto.linha_digitavel}`
          : 'Boleto registrado na Ailos.',
      );
    } catch (err) { setError(parseError(err)); } finally { setBoletoLoading(false); }
  }

  // Consulta a Ailos e, se o boleto estiver pago, dá baixa na cobrança.
  async function handleVerificarPagamento() {
    if (!token || !selectedBilling || !canEdit) return;
    setBoletoLoading(true);
    setError('');
    try {
      const res = await apiFetch<{ pago: boolean; mensagem: string }>(
        `/ailos/boletos/${selectedBilling.id}/verificar-pagamento`,
        { method: 'POST' },
        token,
      );
      setFeedback(res.mensagem);
      if (res.pago) {
        setSelectedBilling(null);
        await loadData(token);
      }
    } catch (err) { setError(parseError(err)); } finally { setBoletoLoading(false); }
  }

  /* ─────────────────────────────────────────────────────────────── */
  return (
    <PageShell title="Financeiro" description="Gestão financeira com KPIs, cobranças, inadimplência e cadastro de planos e contratos.">

      {/* Feedback / error toasts */}
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) && <ErrorBanner message={guardError || error} />}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      {/* Sessão Ailos caída = emissão de boletos parada — reconexão em 2 cliques */}
      {ailosStatus && ailosStatus.cooperado_status !== 'authorized' && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          <span className="font-semibold">⚠ Sessão Ailos desconectada — a emissão de boletos está parada.</span>
          {user?.role === 'admin' ? (
            <>
              <Button
                onClick={handleReconnectAilos}
                disabled={connectingAilos}
                className="!py-1.5 text-xs"
              >
                {connectingAilos ? 'Gerando link…' : 'Reconectar Ailos'}
              </Button>
              <span className="text-xs">
                Abre o login do cooperado em nova aba — após autorizar, clique em
              </span>
              <button
                type="button"
                onClick={refreshAilosStatus}
                className="text-xs font-semibold underline hover:text-amber-900 dark:hover:text-amber-200"
              >
                Verificar conexão
              </button>
            </>
          ) : (
            <span className="text-xs">Peça a um administrador para reconectar (botão disponível no perfil admin).</span>
          )}
        </div>
      )}
      {guardLoading && <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p>}

      {/* ── 1. Inadimplência alert — aparece APENAS quando há cobranças vencidas ── */}
      {(summary?.overdue_billings ?? 0) > 0 && (
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 rounded-2xl border border-red-200 bg-red-50 px-5 py-4 dark:border-red-900/40 dark:bg-red-950/30">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
            <div>
              <p className="font-semibold text-red-700 dark:text-red-300">
                {delinquents.length} cliente{delinquents.length !== 1 ? 's' : ''} com cobranças vencidas
              </p>
              <p className="mt-0.5 text-sm text-red-600 dark:text-red-400">
                {summary?.overdue_billings ?? 0} cobrança{(summary?.overdue_billings ?? 0) !== 1 ? 's' : ''} em atraso
                {' · '}Total: <strong>{formatCurrency(summary?.overdue_amount ?? 0)}</strong>
              </p>
            </div>
          </div>
          <Button
            variant="danger"
            className="shrink-0"
            onClick={() => {
              setBillingView('alert');
              if (activeTab !== 'overview') switchTab('overview');
              setTimeout(() => document.getElementById('billing-section')?.scrollIntoView({ behavior: 'smooth' }), 100);
            }}
          >
            Ver cobranças
          </Button>
        </div>
      )}

      {/* ── 2. Tab navigation ── */}
      <div
        role="tablist"
        aria-label="Seções do Financeiro"
        className="mb-6 flex gap-1 rounded-2xl border border-slate-200 bg-slate-50 p-1 dark:border-slate-800 dark:bg-slate-900/60"
        onKeyDown={(e) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
          e.preventDefault();
          const idx = TAB_ORDER.indexOf(activeTab);
          const next =
            e.key === 'Home' ? 0
            : e.key === 'End' ? TAB_ORDER.length - 1
            : e.key === 'ArrowLeft' ? (idx - 1 + TAB_ORDER.length) % TAB_ORDER.length
            : (idx + 1) % TAB_ORDER.length;
          const nextTab = TAB_ORDER[next];
          switchTab(nextTab);
          document.getElementById(`financeiro-tab-${nextTab}`)?.focus();
        }}
      >
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            type="button"
            id={`financeiro-tab-${tab}`}
            role="tab"
            aria-selected={activeTab === tab}
            aria-controls={`financeiro-panel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => switchTab(tab)}
            className={[
              'flex-1 rounded-xl px-4 py-2.5 text-sm font-semibold transition-all',
              activeTab === tab
                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200',
            ].join(' ')}
          >
            {tab === 'menu' ? 'Menu' : tab === 'overview' ? 'Visão Geral' : tab === 'management' ? 'Planos e Contratos' : 'Contas a Pagar'}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════════
          TAB: Menu (grade de atalhos no padrão do sistema de referência)
      ══════════════════════════════════════════════════════════════ */}
      {activeTab === 'menu' && (
        <section id="financeiro-panel-menu" role="tabpanel" aria-labelledby="financeiro-tab-menu" tabIndex={0} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {([
            { label: 'Fechamento', Icon: Lock, run: () => { window.location.href = '/fechamento'; } },
            { label: 'Alterar situação boleto', Icon: PenSquare, run: () => setCarteiraMode('situacao') },
            { label: 'Alterar situação boleto em lote', Icon: ListChecks, run: () => setCarteiraMode('situacao-lote') },
            { label: 'Manutenção de título', Icon: Banknote, run: () => setCarteiraMode('manutencao') },
            { label: 'Manutenção de título em lote', Icon: Layers, run: () => setCarteiraMode('manutencao-lote') },
            { label: 'Enviar boletos via e-mail / WhatsApp', Icon: Mail, run: () => setEnvioModal(true) },
            { label: 'Módulo de Gestor Financeiro (MGF)', Icon: PieChart, run: () => switchTab('overview') },
            { label: 'Emitir boleto avulso', Icon: Barcode, run: () => { setModalError(''); setNewBillingModal(true); } },
            { label: 'Gerar carnê', Icon: BookText, run: openCarneModal },
            { label: 'Planos e Serviços (tabela de preços)', Icon: Tags, run: () => switchTab('management') },
            { label: 'Contas a Pagar', Icon: Wallet, run: () => switchTab('payables') },
            { label: 'Contas a Receber', Icon: Coins, run: () => setCarteiraMode('full') },
            { label: 'Cadastrar Contas', Icon: FilePlus, run: () => { setModalError(''); setPayableModal(true); } },
          ] as { label: string; Icon: React.ElementType; run: () => void }[]).map(({ label, Icon, run }) => (
            <button
              key={label}
              type="button"
              onClick={run}
              className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-8 text-center text-sm font-semibold text-slate-700 shadow-sm transition hover:border-brand-400 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-brand-500"
            >
              <Icon className="h-6 w-6 text-slate-500 dark:text-slate-400" />
              {label}
            </button>
          ))}
        </section>
      )}

      {/* ══════════════════════════════════════════════════════════════
          TAB: Visão Geral
      ══════════════════════════════════════════════════════════════ */}
      {activeTab === 'overview' && (
        <div id="financeiro-panel-overview" role="tabpanel" aria-labelledby="financeiro-tab-overview" tabIndex={0}>
          <section className="space-y-6">
            {/* CNAB export buttons */}
            {token && canEdit && (
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Exportar remessa:</span>
                {(['cnab400', 'cnab240'] as const).map(fmt => (
                  <button
                    key={fmt}
                    type="button"
                    onClick={async () => {
                      try {
                        const resp = await fetch(
                          `${API_URL.replace(/\/+$/, '')}/boletos/${fmt}`,
                          { method: 'POST', headers: { Authorization: `Bearer ${token}` } }
                        );
                        if (!resp.ok) throw new Error(`Erro ${resp.status}`);
                        const blob = await resp.blob();
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `remessa_${fmt}_${new Date().toISOString().slice(0, 10)}.rem`;
                        a.click();
                        URL.revokeObjectURL(url);
                      } catch (e) {
                        setError(e instanceof Error ? e.message : `Erro ao gerar ${fmt}`);
                      }
                    }}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                  >
                    ⬇ {fmt.toUpperCase()}
                  </button>
                ))}
              </div>
            )}

            {/* Billing table */}
            <BillingTableSection
              billings={billings}
              loading={loading}
              error={error}
              billingView={billingView}
              billingSearch={billingSearch}
              billingStatusFilter={billingStatusFilter}
              onViewToggle={() => setBillingView(billingView === 'alert' ? 'all' : 'alert')}
              onSearchChange={setBillingSearch}
              onStatusFilterChange={setBillingStatusFilter}
              onRefresh={() => token && loadData(token)}
              onSelect={b => setSelectedBilling(b)}
              selectedId={selectedBilling?.id}
              onNewBilling={canEdit ? () => { setModalError(''); setNewBillingModal(true); } : undefined}
              batchIds={selectedBillingIds}
              onBatchIdsChange={canEdit ? setSelectedBillingIds : undefined}
              onBatchReceive={() => setBatchReceiveModal(true)}
              onBatchCancel={handleBatchCancel}
              onBatchMaint={() => { setModalError(''); setBatchMaintModal(true); }}
            />

            {/* ── Indicadores + gráficos no rodapé ── */}
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div>
                <StatCard label="Recebido no mês" value={formatCurrency(summary?.paid_this_month ?? 0)} hint="Receita consolidada" tone="success" icon={<TrendingUp className="h-5 w-5" />} />
                {monthlyVariation !== null ? (
                  <p className={`mt-1.5 text-xs font-semibold ${monthlyVariation >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                    {monthlyVariation >= 0 ? '↑' : '↓'} {Math.abs(monthlyVariation).toFixed(1)}% vs mês anterior
                  </p>
                ) : (
                  <p className="mt-1.5 text-xs text-slate-400">— vs mês anterior</p>
                )}
              </div>
              <div>
                <StatCard label="Pendentes" value={summary?.pending_billings ?? 0} hint={formatCurrency(summary?.pending_amount ?? 0)} tone="brand" icon={<Clock className="h-5 w-5" />} />
                <p className="mt-1.5 text-xs text-slate-400">— vs mês anterior</p>
              </div>
              <div>
                <StatCard label="Vencidas" value={summary?.overdue_billings ?? 0} hint={formatCurrency(summary?.overdue_amount ?? 0)} tone="danger" icon={<AlertTriangle className="h-5 w-5" />} />
                <p className="mt-1.5 text-xs text-slate-400">— vs mês anterior</p>
              </div>
              <div>
                <StatCard label="Contratos ativos" value={summary?.active_contracts ?? 0} hint={`${summary?.active_plans ?? 0} plano(s) em uso`} icon={<FileText className="h-5 w-5" />} />
                <p className="mt-1.5 text-xs text-slate-400">— vs mês anterior</p>
              </div>
            </div>

            <Card>
              <div className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                  <p className="mb-1 text-xs font-bold uppercase tracking-widest text-slate-500">Faturamento mensal</p>
                  <p className="mb-3 text-xs text-slate-400">Receita recebida por mês · passe o mouse para detalhes</p>
                  <BarChart
                    items={revenue.slice(-8).map(r => ({ label: r.label, value: r.total_received, secondaryValue: r.total_billed }))}
                    formatValue={formatCurrency}
                    height={220}
                    emptyMessage="Sem dados de faturamento."
                  />
                </div>
                <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                  <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-500">
                    Clientes inadimplentes{delinquents.length > 0 ? ` (${delinquents.length})` : ''}
                  </p>
                  <div className="space-y-2">
                    {error ? (
                      <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                        <AlertTriangle className="h-4 w-4 text-amber-500" />
                        Não foi possível carregar — veja o erro acima.
                      </div>
                    ) : delinquents.length === 0 ? (
                      <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        Nenhum cliente inadimplente no momento.
                      </div>
                    ) : delinquents.slice(0, 5).map(item => (
                      <div key={item.client_id} className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm dark:border-slate-800 dark:bg-slate-900">
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-slate-900 dark:text-white">{item.client_name}</p>
                          <p className="text-xs text-slate-500">{item.overdue_count} cobrança(s) vencida(s)</p>
                        </div>
                        <span className="ml-3 shrink-0 font-bold text-rose-600 dark:text-rose-400">{formatCurrency(item.total_open)}</span>
                      </div>
                    ))}
                    {delinquents.length > 5 && (
                      <p className="text-xs text-slate-400">+ {delinquents.length - 5} outros clientes</p>
                    )}
                  </div>
                </div>
              </div>
            </Card>
          </section>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════
          TAB: Planos e Contratos
      ══════════════════════════════════════════════════════════════ */}
      {activeTab === 'management' && (
        <section id="financeiro-panel-management" role="tabpanel" aria-labelledby="financeiro-tab-management" tabIndex={0} className="space-y-6">
          {/* Quick actions */}
          <Card>
            <SectionHeader
              eyebrow="Ações rápidas"
              title="Cadastros e lançamentos"
              actions={
                <div className="flex flex-wrap gap-2">
                  <Button onClick={openCreatePlan} variant="secondary">Novo plano</Button>
                  <Button onClick={openCreateProduct} variant="secondary">Novo serviço</Button>
                  <Button onClick={() => setChargeModal(true)}>Novo lançamento</Button>
                </div>
              }
            />
          </Card>

          {/* ── 2. Plans table with RowMenu + sort ── */}
          <Card>
            <div className="mb-3 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
                Planos ({plans.length})
              </p>
              <Button variant="secondary" onClick={openCreatePlan} className="text-xs px-3 py-1.5">
                Adicionar plano
              </Button>
            </div>
            {error ? (
              <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os planos" description="Veja o erro acima e tente novamente." />
            ) : plans.length === 0 ? (
              <EmptyState title="Nenhum plano" description="Crie o primeiro plano de serviço." />
            ) : (
              <Table>
                <TableHead>
                  <th
                    className="cursor-pointer select-none py-3 pl-4 pr-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-700"
                    onClick={() => togglePlanSort('name')}
                  >
                    Nome <SortIndicator field="name" />
                  </th>
                  <Th>Periodicidade</Th>
                  <th
                    className="cursor-pointer select-none px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-700"
                    onClick={() => togglePlanSort('price')}
                  >
                    Valor <SortIndicator field="price" />
                  </th>
                  <th
                    className="cursor-pointer select-none px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-700"
                    onClick={() => togglePlanSort('active_contracts')}
                  >
                    Contratos <SortIndicator field="active_contracts" />
                  </th>
                  <Th className="w-12" />
                </TableHead>
                <TableBody>
                  {sortedPlans.map(p => (
                    <Tr key={p.id}>
                      <Td><p className="font-medium">{p.name}</p></Td>
                      <Td>{intervalLabel(p.billing_interval_months)}</Td>
                      <Td className="font-mono">{formatCurrency(p.price)}</Td>
                      <Td>
                        <Badge variant={p.active ? 'success' : 'default'}>
                          {p.active ? 'Ativo' : 'Inativo'} · {p.active_contracts}
                        </Badge>
                      </Td>
                      <Td>
                        <div className="flex justify-end">
                          {canEdit && <RowMenu onEdit={() => openEditPlan(p)} onDelete={() => handleDeletePlan(p)} disabled={processing} />}
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>

          {/* ── Serviços / produtos: catálogo com preço-base editável ── */}
          <Card>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
                  Serviços e produtos ({serviceProducts.length})
                </p>
                <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                  Preço-base de cada serviço (instalação, desinstalação, visita técnica…). O valor pode ser ajustado no lançamento.
                </p>
              </div>
              <Button variant="secondary" onClick={openCreateProduct} className="text-xs px-3 py-1.5">
                Adicionar serviço
              </Button>
            </div>
            {error ? (
              <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os serviços" description="Veja o erro acima e tente novamente." />
            ) : serviceProducts.length === 0 ? (
              <EmptyState title="Nenhum serviço" description="Cadastre os serviços cobrados (instalação, desinstalação, visita técnica…)." />
            ) : (
              <Table>
                <TableHead>
                  <Th>Nome</Th>
                  <Th>Categoria</Th>
                  <Th>Preço base</Th>
                  <Th>Parcelamento</Th>
                  <Th>Automático</Th>
                  <Th>Situação</Th>
                  <Th className="w-12" />
                </TableHead>
                <TableBody>
                  {[...serviceProducts]
                    .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'))
                    .map(sp => (
                      <Tr key={sp.id}>
                        <Td>
                          <p className="font-medium">{sp.name}</p>
                          {sp.description && <p className="text-xs text-slate-400">{sp.description}</p>}
                        </Td>
                        <Td className="text-xs capitalize">{sp.category || '—'}</Td>
                        <Td className="font-mono font-semibold">{formatCurrency(sp.default_price)}</Td>
                        <Td className="text-xs">{sp.allow_installments ? 'Permite' : 'À vista'}</Td>
                        <Td className="text-xs">{sp.auto_add_on_uninstall ? 'Na desinstalação' : '—'}</Td>
                        <Td>
                          <Badge variant={sp.active ? 'success' : 'default'}>{sp.active ? 'Ativo' : 'Inativo'}</Badge>
                        </Td>
                        <Td>
                          <div className="flex justify-end">
                            {canEdit && <RowMenu onEdit={() => openEditProduct(sp)} onDelete={() => handleDeleteProduct(sp)} disabled={processing} />}
                          </div>
                        </Td>
                      </Tr>
                    ))}
                </TableBody>
              </Table>
            )}
          </Card>

          {/* ── 3. Contratos: gerar o termo de adesão para o cliente assinar ── */}
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Contratos</p>
                <p className="mt-1 max-w-xl text-sm text-slate-500 dark:text-slate-400">
                  Gere o termo de adesão em branco com o <strong>plano</strong>, a <strong>vigência</strong> e as
                  <strong> taxas</strong>. Nada é salvo: baixe o PDF, envie ao cliente para preencher os dados dele e
                  assinar, e depois guarde o assinado em <strong>Clientes → contrato</strong>.
                </p>
              </div>
              <Button variant="secondary" onClick={openCreateContract} className="shrink-0">
                Gerar contrato
              </Button>
            </div>
          </Card>
        </section>
      )}

      {/* ══════════════════════════════════════════════════════════════
          TAB: Contas a Pagar
      ══════════════════════════════════════════════════════════════ */}
      {activeTab === 'payables' && (
        <section id="financeiro-panel-payables" role="tabpanel" aria-labelledby="financeiro-tab-payables" tabIndex={0} className="space-y-6">
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <SectionHeader eyebrow="Contas a Pagar" title="Despesas da empresa" />
              <div className="flex gap-2">
                {canEdit && (
                  <Button onClick={() => { setModalError(''); setPayableModal(true); }} className="text-xs px-3 py-1.5">
                    Cadastrar conta
                  </Button>
                )}
                <select className={fieldClass} style={{ width: 160 }} value={payableStatusFilter} onChange={e => setPayableStatusFilter(e.target.value)}>
                  <option value="">Todas</option>
                  <option value="pendente">Pendentes</option>
                  <option value="paga">Pagas</option>
                  <option value="cancelada">Canceladas</option>
                </select>
              </div>
            </div>

            {(() => {
              const list = payableStatusFilter ? payables.filter(p => p.status === payableStatusFilter) : payables;
              const pendentes = payables.filter(p => p.status === 'pendente');
              const totalPendente = pendentes.reduce((s, p) => s + p.amount, 0);
              const vencidas = pendentes.filter(p => p.overdue_days > 0);
              return (
                <>
                  <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-500 dark:text-slate-400">
                    <span>A pagar: <strong className="font-mono text-slate-900 dark:text-white">{formatCurrency(totalPendente)}</strong> ({pendentes.length} conta(s))</span>
                    {vencidas.length > 0 && (
                      <span className="text-rose-600 dark:text-rose-400">
                        {vencidas.length} vencida(s): <strong className="font-mono">{formatCurrency(vencidas.reduce((s, p) => s + p.amount, 0))}</strong>
                      </span>
                    )}
                  </div>
                  <div className="mt-4">
                    {list.length === 0 ? (
                      <EmptyState icon={FileText} title="Nenhuma conta" description="Cadastre as despesas da empresa (fornecedores, aluguel, chips…)." />
                    ) : (
                      <Table>
                        <TableHead>
                          <Th>Descrição</Th>
                          <Th>Fornecedor</Th>
                          <Th>Categoria</Th>
                          <Th>Vencimento</Th>
                          <Th>Valor</Th>
                          <Th>Status</Th>
                          <Th className="w-52" />
                        </TableHead>
                        <TableBody>
                          {list.map(p => (
                            <Tr key={p.id}>
                              <Td><p className="font-medium">{p.description}</p></Td>
                              <Td className="text-sm">{p.supplier ?? '—'}</Td>
                              <Td className="text-xs capitalize">{p.category ?? '—'}</Td>
                              <Td>
                                <p className="text-sm">{formatDate(p.due_date)}</p>
                                {p.overdue_days > 0 && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{p.overdue_days}d atraso</p>}
                              </Td>
                              <Td className="font-mono font-semibold">{formatCurrency(p.amount)}</Td>
                              <Td><Badge variant={p.status === 'paga' ? 'success' : p.status === 'cancelada' ? 'default' : p.overdue_days > 0 ? 'danger' : 'warning'}>{p.status === 'paga' ? 'Paga' : p.status === 'cancelada' ? 'Cancelada' : p.overdue_days > 0 ? 'Vencida' : 'Pendente'}</Badge></Td>
                              <Td>
                                {canEdit && p.status === 'pendente' && (
                                  <div className="flex justify-end gap-2">
                                    <Button onClick={() => { setPayingPayable(p); setPayPayableForm({ payment_date: new Date().toISOString().slice(0, 10), payment_method: 'pix' }); }} className="px-3 py-1.5 text-xs">Pagar</Button>
                                    <Button variant="secondary" onClick={() => handleCancelPayable(p)} className="px-3 py-1.5 text-xs">Cancelar</Button>
                                    <Button variant="secondary" onClick={() => handleDeletePayable(p)} className="px-3 py-1.5 text-xs">Excluir</Button>
                                  </div>
                                )}
                              </Td>
                            </Tr>
                          ))}
                        </TableBody>
                      </Table>
                    )}
                  </div>
                </>
              );
            })()}
          </Card>
        </section>
      )}

      {/* ── Modal: Carteira de cobranças (cards do Menu) ─────────────────────
          IMPORTANTE: renderizado ANTES dos modais de detalhe/receber/ajustar
          para que eles empilhem POR CIMA desta carteira (ordem do DOM). */}
      {(() => {
        const titulos: Record<CarteiraMode, { t: string; s: string; rotulo?: string; lote?: Array<'receive' | 'cancel' | 'maint'> }> = {
          'full':            { t: 'Contas a Receber',                s: 'Carteira completa de cobranças' },
          'situacao':        { t: 'Alterar situação boleto',        s: 'Clique em "Alterar situação" para registrar o pagamento (ou cancelar pelo detalhe)', rotulo: 'Alterar situação' },
          'situacao-lote':   { t: 'Alterar situação em lote',       s: 'Marque os boletos e use Receber/Cancelar em lote', lote: ['receive', 'cancel'] },
          'manutencao':      { t: 'Manutenção de título',           s: 'Clique em "Manutenção" para alterar valor/vencimento com justificativa', rotulo: 'Manutenção' },
          'manutencao-lote': { t: 'Manutenção de título em lote',   s: 'Marque os títulos e use "Alterar venc./valor"', lote: ['maint'] },
        };
        const cfg = carteiraMode ? titulos[carteiraMode] : null;
        return (
          <Modal
            open={!!carteiraMode}
            onClose={() => { setCarteiraMode(null); setSelectedBillingIds([]); }}
            title={cfg?.t ?? 'Carteira de cobranças'}
            subtitle={cfg?.s}
            size="2xl"
          >
            <BillingTableSection
              billings={billings}
              loading={loading}
              error={error}
              billingView="all"
              billingSearch={billingSearch}
              billingStatusFilter={billingStatusFilter}
              onViewToggle={() => {}}
              onSearchChange={setBillingSearch}
              onStatusFilterChange={setBillingStatusFilter}
              onRefresh={() => token && loadData(token)}
              onSelect={carteiraRowSelect}
              selectedId={selectedBilling?.id}
              onNewBilling={canEdit ? () => { setModalError(''); setNewBillingModal(true); } : undefined}
              batchIds={selectedBillingIds}
              onBatchIdsChange={canEdit && (carteiraMode === 'full' || carteiraMode?.endsWith('-lote')) ? setSelectedBillingIds : undefined}
              onBatchReceive={() => setBatchReceiveModal(true)}
              onBatchCancel={handleBatchCancel}
              onBatchMaint={() => { setModalError(''); setBatchMaintModal(true); }}
              batchActions={cfg?.lote}
              rowActionLabel={cfg?.rotulo}
            />
          </Modal>
        );
      })()}

      {/* ── Modals (unchanged) ── */}

      <Modal open={!!selectedBilling} onClose={() => setSelectedBilling(null)} title={selectedBilling?.title ?? selectedBilling?.payer_name ?? selectedBilling?.client_name ?? 'Cobrança'} subtitle="Detalhes da cobrança" size="md">
        {selectedBilling && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              {([
                ['Cliente atendido', selectedBilling.client_name ?? '—'],
                ['Responsável financeiro', selectedBilling.payer_name ?? selectedBilling.client_name ?? '—'],
                ['Status', <Badge key="s" variant={statusVariant(selectedBilling.status)}>{statusLabel(selectedBilling.status)}</Badge>],
                ['Valor', formatCurrency(selectedBilling.amount)],
                ['Vencimento', selectedBilling.due_date],
                ['Veículo', selectedBilling.vehicle_plate ?? '—'],
                ['Período', selectedBilling.period_label ?? '—'],
                ...(selectedBilling.installment_number ? [['Parcela', `${selectedBilling.installment_number}/${selectedBilling.installment_total}`] as [string, string]] : []),
                ...(selectedBilling.paid_amount != null ? [['Valor pago', formatCurrency(selectedBilling.paid_amount)] as [string, string]] : []),
              ] as [string, React.ReactNode][]).map(([label, value]) => (
                <div key={String(label)} className="rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">{label}</p>
                  <div className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</div>
                </div>
              ))}
            </div>
            {selectedBilling.overdue_days > 0 && (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">
                {selectedBilling.overdue_days} dia(s) em atraso
              </div>
            )}
            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
              <Button disabled={!canEdit || processing || selectedBilling.status === 'paga' || selectedBilling.status === 'cancelada'} onClick={() => setReceiveModal(true)}>Registrar pagamento</Button>
              <Button variant="secondary" disabled={!canEdit || processing || selectedBilling.status === 'cancelada'} onClick={() => setAdjustModal(true)}>Ajustar cobrança</Button>
              <Button variant="danger" disabled={!canEdit || processing || selectedBilling.status === 'cancelada'} onClick={handleCancel}>Cancelar</Button>
              {(selectedBilling.status === 'pendente' || selectedBilling.status === 'vencida') && token && (
                <Button variant="secondary" disabled={!canEdit || boletoLoading} onClick={handleGerarBoleto}>
                  {boletoLoading ? 'Gerando…' : '🔑 Gerar boleto (Ailos)'}
                </Button>
              )}
              {(selectedBilling.status === 'pendente' || selectedBilling.status === 'vencida') && token && (
                <Button variant="secondary" disabled={!canEdit || boletoLoading} onClick={handleVerificarPagamento}>
                  {boletoLoading ? 'Verificando…' : '🔄 Verificar pagamento'}
                </Button>
              )}
              {/* Pago é a única condição: exigir receipt_number escondia o
                  recibo de cobranças pagas antes de o campo existir. */}
              {selectedBilling.status === 'paga' && (
                <Button variant="secondary" onClick={() => { if (!token) return; downloadProtectedFile(`/billings/${selectedBilling.id}/receipt`, token, `recibo-${selectedBilling.receipt_number ?? selectedBilling.id}.pdf`).catch(e => setError(parseError(e))); }}>Baixar recibo</Button>
              )}
            </div>

            {/* ── NFS-e (Joinville) ── */}
            <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Nota Fiscal (NFS-e)</p>
                {nfse?.status === 'emitida' && <Badge variant="success">Emitida</Badge>}
                {nfse?.status === 'processing' && <Badge variant="warning">Processando</Badge>}
                {nfse?.status === 'erro' && <Badge variant="danger">Erro</Badge>}
              </div>

              {nfse?.status === 'emitida' ? (
                <div className="mt-2 space-y-1 text-sm text-slate-700 dark:text-slate-200">
                  <p>Número <strong>{nfse.numero_nfse}</strong>{nfse.serie_nfse ? ` · Série ${nfse.serie_nfse}` : ''}</p>
                  {nfse.codigo_verificacao && <p className="text-xs text-slate-500">Cód. verificação: {nfse.codigo_verificacao}</p>}
                  <div className="flex items-center gap-3 pt-1">
                    <button
                      type="button"
                      onClick={() => abrirNotaPdf(selectedBilling.id)}
                      className="text-sm font-semibold text-brand-700 hover:underline dark:text-brand-400"
                    >
                      Visualizar NFS-e (PDF)
                    </button>
                    {nfse.link_visualizacao && (
                      <a href={nfse.link_visualizacao} target="_blank" rel="noopener noreferrer" className="text-xs font-medium text-slate-500 hover:underline dark:text-slate-400">Consulta pública ↗</a>
                    )}
                  </div>
                </div>
              ) : nfse?.status === 'erro' ? (
                <div className="mt-2 space-y-2">
                  <p className="text-sm text-rose-600 dark:text-rose-400">{nfse.erro_mensagem || 'Falha ao emitir a NFS-e.'}</p>
                  <Button variant="secondary" disabled={!canEdit || nfseLoading} onClick={handleEmitirNfse}>{nfseLoading ? 'Emitindo…' : 'Tentar novamente'}</Button>
                </div>
              ) : nfse?.status === 'processing' ? (
                <div className="mt-2 space-y-2">
                  <p className="text-sm text-slate-500">Lote enviado{nfse.protocolo ? ` (protocolo ${nfse.protocolo})` : ''}. Aguardando processamento.</p>
                  <Button variant="secondary" disabled={nfseLoading} onClick={handleConsultarNfse}>{nfseLoading ? 'Consultando…' : 'Atualizar status'}</Button>
                </div>
              ) : (
                <div className="mt-2">
                  <p className="mb-2 text-sm text-slate-500">Nenhuma NFS-e emitida para esta cobrança.</p>
                  <Button disabled={!canEdit || nfseLoading || selectedBilling.status === 'cancelada'} onClick={handleEmitirNfse}>{nfseLoading ? 'Emitindo…' : 'Emitir NFS-e'}</Button>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal open={planModal} onClose={() => { setPlanModal(false); setEditingPlanId(null); setPlanForm(initialPlanForm); setModalError(''); }} title={editingPlanId ? 'Editar plano' : 'Novo plano'} description="Cadastre planos com periodicidade mensal, trimestral, semestral ou anual." size="lg">
        <form className="space-y-5" onSubmit={submitPlan}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Nome do plano" value={planForm.name} onChange={e => setPlanForm(p => ({ ...p, name: e.target.value }))} required />
            <input className={fieldClass} placeholder="Valor base" value={planForm.price} onChange={e => setPlanForm(p => ({ ...p, price: e.target.value }))} required />
            <select className={fieldClass} value={planForm.billing_interval_months} onChange={e => setPlanForm(p => ({ ...p, billing_interval_months: e.target.value }))}><option value="1">Mensal</option><option value="3">Trimestral</option><option value="6">Semestral</option><option value="12">Anual</option></select>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={planForm.active} onChange={e => setPlanForm(p => ({ ...p, active: e.target.checked }))} /> Plano ativo</label>
            <div className="md:col-span-2 rounded-2xl border border-slate-200 px-4 py-3 dark:border-slate-700">
              <p className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">Padrões para o contrato (pré-preenchem ao escolher este plano; opcionais)</p>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <input className={fieldClass} placeholder="Taxa de instalação (R$)" value={planForm.default_installation_fee} onChange={e => setPlanForm(p => ({ ...p, default_installation_fee: e.target.value }))} />
                <input className={fieldClass} placeholder="Taxa de desinstalação (R$)" value={planForm.default_uninstall_fee} onChange={e => setPlanForm(p => ({ ...p, default_uninstall_fee: e.target.value }))} />
                <input className={fieldClass} placeholder="Dia de vencimento" value={planForm.default_billing_day} onChange={e => setPlanForm(p => ({ ...p, default_billing_day: e.target.value.replace(/\D/g, "").slice(0, 2) }))} />
                <input className={fieldClass} placeholder="Vigência (meses)" value={planForm.default_duration_months} onChange={e => setPlanForm(p => ({ ...p, default_duration_months: e.target.value.replace(/\D/g, "").slice(0, 3) }))} />
              </div>
            </div>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição do plano" value={planForm.description} onChange={e => setPlanForm(p => ({ ...p, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setPlanModal(false)}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : editingPlanId ? 'Salvar alterações' : 'Cadastrar plano'}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={productModal} onClose={() => { setProductModal(false); setEditingProductId(null); setServiceProductForm(initialProductForm); setModalError(''); }} title={editingProductId ? 'Editar serviço / produto' : 'Novo serviço / produto'} description="Cadastre taxas, acessórios, sensores e outros itens cobrados do cliente." size="lg">
        <form className="space-y-5" onSubmit={submitProduct}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Nome do item" value={serviceProductForm.name} onChange={e => setServiceProductForm(p => ({ ...p, name: e.target.value }))} required />
            <select className={fieldClass} value={serviceProductForm.category} onChange={e => setServiceProductForm(p => ({ ...p, category: e.target.value }))}><option value="servico">Serviço</option><option value="produto">Produto</option><option value="taxa">Taxa</option></select>
            <input className={fieldClass} placeholder="Valor padrão" value={serviceProductForm.default_price} onChange={e => setServiceProductForm(p => ({ ...p, default_price: e.target.value }))} required />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.active} onChange={e => setServiceProductForm(p => ({ ...p, active: e.target.checked }))} /> Item ativo</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.allow_installments} onChange={e => setServiceProductForm(p => ({ ...p, allow_installments: e.target.checked }))} /> Permitir parcelamento</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.remove_after_payment} onChange={e => setServiceProductForm(p => ({ ...p, remove_after_payment: e.target.checked }))} /> Remover após pagamento</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700 md:col-span-2"><input type="checkbox" checked={serviceProductForm.auto_add_on_uninstall} onChange={e => setServiceProductForm(p => ({ ...p, auto_add_on_uninstall: e.target.checked }))} /> Utilizar como taxa de desinstalação</label>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição do item" value={serviceProductForm.description} onChange={e => setServiceProductForm(p => ({ ...p, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setProductModal(false)}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : editingProductId ? 'Salvar alterações' : 'Cadastrar item'}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={contractModal} onClose={() => { setContractModal(false); setEditingContractId(null); setContractForm(initialContractForm); setModalError(''); }} title="Gerar contrato" description="Modelo em branco para o cliente preencher e assinar. Escolha o plano e a vigência — nada é salvo e os dados do cliente saem em branco." size="lg">
        <form className="space-y-5" onSubmit={submitContract}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <label className="md:col-span-2 text-sm">
              <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Plano contratado</span>
              <select className={fieldClass} value={contractForm.plan_id} onChange={e => {
                const id = e.target.value;
                const plano = plans.find(pl => String(pl.id) === id);
                setContractForm(p => {
                  // Ao escolher o plano, puxa os padrões: taxas e vigência (fim).
                  const next = { ...p, plan_id: id };
                  if (plano) {
                    if (plano.default_installation_fee != null) next.installation_fee = String(plano.default_installation_fee);
                    if (plano.default_uninstall_fee != null) next.uninstall_fee = String(plano.default_uninstall_fee);
                    if (plano.default_duration_months != null && p.start_date) next.end_date = addMonthsISO(p.start_date, plano.default_duration_months);
                  }
                  return next;
                });
              }} required><option value="">Selecione o plano</option>{plans.filter(p => p.active || String(p.id) === contractForm.plan_id).map(p => <option key={p.id} value={p.id}>{p.name} • {intervalLabel(p.billing_interval_months)}</option>)}</select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Início da vigência</span>
              <input type="date" className={fieldClass} value={contractForm.start_date} onChange={e => setContractForm(p => ({ ...p, start_date: e.target.value }))} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Fim da vigência <span className="font-normal text-slate-400">(se necessário)</span></span>
              <input type="date" className={fieldClass} value={contractForm.end_date} onChange={e => setContractForm(p => ({ ...p, end_date: e.target.value }))} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Taxa de instalação por veículo (R$)</span>
              <input className={fieldClass} placeholder="0,00" value={contractForm.installation_fee} onChange={e => setContractForm(p => ({ ...p, installation_fee: e.target.value }))} />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Taxa de desinstalação por veículo (R$)</span>
              <input className={fieldClass} placeholder="0,00" value={contractForm.uninstall_fee} onChange={e => setContractForm(p => ({ ...p, uninstall_fee: e.target.value }))} />
            </label>

            <div className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400 md:col-span-2">
              Baixe o PDF e envie ao cliente para preencher os dados dele e assinar. Quando devolver, guarde o assinado em <strong>Clientes → botão de contrato → “Enviar contrato assinado”</strong> — é lá que ele fica listado e o cadastro marca “contrato armazenado”.
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setContractModal(false)}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || processing}>{processing ? 'Gerando...' : 'Gerar e baixar contrato'}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={carneModal} onClose={() => setCarneModal(false)} title="Gerar carnê" description="Gere o carnê a partir dos boletos em aberto do cliente, ou criando parcelas do plano do veículo. Um carnê é sempre de um único cliente." size="xl">
        {modalError && <p className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
        <div className="space-y-4">
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">Cliente</p>
            <ClientAutocomplete clients={clients} value={carneClientId} onChange={carregarBoletosCarne} placeholder="Busque o cliente pelo nome ou CPF/CNPJ" />
          </div>

          {carneClientId && carnesGerados.length > 0 && (
            <div className="rounded-xl border border-slate-200 dark:border-slate-700">
              <p className="border-b border-slate-100 px-3 py-2 text-xs font-semibold uppercase tracking-widest text-slate-500 dark:border-slate-800">Carnês já gerados deste cliente</p>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {carnesGerados.map(cg => (
                  <div key={cg.lote_id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <span className="font-medium text-slate-700 dark:text-slate-200">{cg.parcelas_registradas} de {cg.parcelas} parcela(s)</span>
                      <span className="ml-2 text-xs text-slate-400">{cg.criado_em ? formatDate(cg.criado_em) : ''} · {formatCurrency(cg.total)}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge variant={cg.status === 'completed' ? 'success' : 'warning'}>{cg.status === 'completed' ? 'Completo' : 'Processando'}</Badge>
                      {cg.ticket && (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                          onClick={() => carne.abrirExistente(cg.lote_id, cg.ticket!)}
                        ><Eye className="h-3.5 w-3.5" />Acompanhar</button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Modo: a partir de boletos já existentes ou criando parcelas do plano */}
          <div className="flex gap-1">
            {(['existentes', 'plano'] as const).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => setCarneMode(m)}
                className={['rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
                  carneMode === m ? 'bg-brand-700 text-white' : 'border border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300'].join(' ')}
              >
                {m === 'existentes' ? 'Boletos existentes' : 'A partir do plano'}
              </button>
            ))}
          </div>

          {carneMode === 'existentes' ? (carneLoading ? (
            <p className="text-sm text-slate-500">Carregando boletos do cliente…</p>
          ) : carneClientId && carneBillings.length === 0 ? (
            <p className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Este cliente não tem boletos pendentes ou vencidos para gerar carnê.</p>
          ) : carneBillings.length > 0 ? (
            <>
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Boletos em aberto ({carneBillings.length})</p>
                <div className="flex items-center gap-3 text-xs">
                  <button type="button" onClick={() => setCarneSelected(carneBillings.map(b => b.id))} className="font-semibold text-brand-700 hover:underline dark:text-brand-400">Selecionar todos</button>
                  <button type="button" onClick={() => setCarneSelected([])} className="text-slate-400 hover:underline">Limpar</button>
                </div>
              </div>
              <div className="max-h-72 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-700">
                {carneBillings.map(b => (
                  <label key={b.id} className="flex cursor-pointer items-center gap-3 border-b border-slate-100 px-3 py-2 text-sm last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                    <input type="checkbox" className="rounded border-slate-300" checked={carneSelected.includes(b.id)} onChange={() => setCarneSelected(prev => prev.includes(b.id) ? prev.filter(id => id !== b.id) : [...prev, b.id])} />
                    <span className="flex-1 truncate">{b.title || 'Cobrança'}{b.vehicle_plate ? ` · ${b.vehicle_plate}` : ''}{b.period_label ? ` · ${b.period_label}` : ''}</span>
                    <span className="whitespace-nowrap text-xs text-slate-500">venc. {formatDate(b.due_date)}</span>
                    <Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge>
                    <span className="w-24 text-right font-mono font-semibold text-slate-900 dark:text-white">{formatCurrency(b.amount)}</span>
                  </label>
                ))}
              </div>
              <p className="text-xs text-slate-400">{carneSelected.length} de {carneBillings.length} selecionado(s). O carnê registra <strong>1 boleto real por parcela</strong> na Ailos.</p>
            </>
          ) : (
            <p className="text-sm text-slate-400">Selecione um cliente para listar os boletos.</p>
          )) : !carneClientId ? (
            <p className="text-sm text-slate-400">Selecione um cliente para escolher o contrato.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="sm:col-span-2 text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Contrato (plano do veículo)</span>
                <select className={fieldClass} value={carneContractId} onChange={e => {
                  const id = e.target.value;
                  const ct = contracts.find(c => String(c.id) === id);
                  setCarneContractId(id);
                  if (ct?.monthly_value != null) setCarneValor(String(ct.monthly_value));
                }}>
                  <option value="">Selecione o contrato</option>
                  {contracts
                    .filter(c => String(c.client_id) === carneClientId && c.status === 'ativo')
                    .map(c => (
                      <option key={c.id} value={c.id}>#{c.id} • {c.plan_name || 'Plano'}{c.vehicle_plate ? ` • ${c.vehicle_plate}` : ''}{c.monthly_value != null ? ` • ${formatCurrency(c.monthly_value)}` : ''}</option>
                    ))}
                </select>
              </label>
              <label className="text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Quantidade de parcelas</span>
                <input className={fieldClass} inputMode="numeric" value={carneNumParcelas} onChange={e => setCarneNumParcelas(e.target.value.replace(/\D/g, '').slice(0, 2))} placeholder="12" />
              </label>
              <label className="text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Valor de cada parcela (R$)</span>
                <input className={fieldClass} value={carneValor} onChange={e => setCarneValor(e.target.value)} placeholder="valor do plano" />
              </label>
              <label className="sm:col-span-2 text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Primeiro vencimento <span className="font-normal text-slate-400">(opcional — padrão: próximo vencimento do contrato)</span></span>
                <input type="date" className={fieldClass} value={carnePrimeiroVenc} onChange={e => setCarnePrimeiroVenc(e.target.value)} />
              </label>
              <p className="sm:col-span-2 text-xs text-slate-400">Serão criadas {Number(carneNumParcelas) || 0} parcelas mensais do plano e registradas na Ailos como carnê (1 boleto por parcela).</p>
            </div>
          )}
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setCarneModal(false)}>Cancelar</button>
          <Button onClick={carneMode === 'existentes' ? () => gerarCarne(carneSelected) : gerarCarneDoPlano} disabled={gerandoCarne || (carneMode === 'existentes' ? carneSelected.length < 2 : (!carneContractId || Number(carneNumParcelas) < 2))}>{gerandoCarne ? 'Gerando carnê…' : carneMode === 'existentes' ? `Gerar carnê (${carneSelected.length})` : `Gerar carnê (${Number(carneNumParcelas) || 0} parcelas)`}</Button>
        </div>
      </Modal>

      <CarneTrackingModal carne={carne} />

      <Modal open={chargeModal} onClose={() => { setChargeModal(false); setModalError(''); }} title="Novo lançamento financeiro" description="Lance serviços, produtos e taxas com opção de parcelamento e remoção após pagamento." size="xl">
        <form className="space-y-5" onSubmit={submitChargeItem}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <ClientAutocomplete
              clients={clients}
              value={chargeItemForm.client_id}
              onChange={(id) => setChargeItemForm(p => ({ ...p, client_id: id, contract_id: '', vehicle_id: '', tracker_id: '' }))}
              placeholder="Selecione o cliente"
              required
            />
            <select className={fieldClass} value={chargeItemForm.contract_id} onChange={e => { const sel = visibleContracts.find(c => c.id === Number(e.target.value)); setChargeItemForm(p => ({ ...p, contract_id: e.target.value, vehicle_id: sel?.vehicle_id ? String(sel.vehicle_id) : p.vehicle_id, tracker_id: sel?.tracker_id ? String(sel.tracker_id) : p.tracker_id })); }}><option value="">Sem contrato</option>{visibleContracts.map(c => <option key={c.id} value={c.id}>{c.client_name} • {c.plan_name || 'Contrato'}{c.vehicle_plate ? ` • ${c.vehicle_plate}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.vehicle_id} onChange={e => setChargeItemForm(p => ({ ...p, vehicle_id: e.target.value, tracker_id: '' }))}><option value="">Sem veículo</option>{chargeVehicles.map(v => <option key={v.id} value={v.id}>{v.plate}{v.model ? ` • ${v.model}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.tracker_id} onChange={e => setChargeItemForm(p => ({ ...p, tracker_id: e.target.value }))}><option value="">Sem rastreador</option>{chargeTrackers.map(t => <option key={t.id} value={t.id}>{t.imei}{t.model ? ` • ${t.model}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.service_product_id} onChange={e => { const sel = serviceProducts.find(sp => sp.id === Number(e.target.value)); setChargeItemForm(p => ({ ...p, service_product_id: e.target.value, title: sel?.name || p.title, unit_price: sel ? String(sel.default_price) : p.unit_price, remove_after_payment: sel?.remove_after_payment || false })); }}><option value="">Selecione um serviço/produto</option>{serviceProducts.filter(sp => sp.active).map(sp => <option key={sp.id} value={sp.id}>{sp.name}</option>)}</select>
            <input className={fieldClass} placeholder="Título da cobrança" value={chargeItemForm.title} onChange={e => setChargeItemForm(p => ({ ...p, title: e.target.value }))} required />
            <input className={fieldClass} placeholder="Quantidade" value={chargeItemForm.quantity} onChange={e => setChargeItemForm(p => ({ ...p, quantity: e.target.value.replace(/\D/g, '').slice(0, 3) }))} />
            <input className={fieldClass} placeholder="Valor unitário" value={chargeItemForm.unit_price} onChange={e => setChargeItemForm(p => ({ ...p, unit_price: e.target.value }))} required />
            <input className={fieldClass} placeholder="Quantidade de parcelas" value={chargeItemForm.installment_count} onChange={e => setChargeItemForm(p => ({ ...p, installment_count: e.target.value.replace(/\D/g, '').slice(0, 3) }))} />
            <input type="date" className={fieldClass} value={chargeItemForm.start_date} onChange={e => setChargeItemForm(p => ({ ...p, start_date: e.target.value }))} />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700 md:col-span-2"><input type="checkbox" checked={chargeItemForm.remove_after_payment} onChange={e => setChargeItemForm(p => ({ ...p, remove_after_payment: e.target.checked }))} /> Remover item após pagamento total das parcelas</label>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição" value={chargeItemForm.description} onChange={e => setChargeItemForm(p => ({ ...p, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setChargeModal(false)}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : 'Criar lançamento'}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={receiveModal} onClose={() => { setReceiveModal(false); setModalError(''); }} title="Registrar pagamento" description="Confirme o recebimento da cobrança selecionada e gere o recibo em PDF após a baixa." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Valor pago" value={receiveForm.paid_amount} onChange={e => setReceiveForm(p => ({ ...p, paid_amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={receiveForm.payment_date} onChange={e => setReceiveForm(p => ({ ...p, payment_date: e.target.value }))} />
            <select className={fieldClass} value={receiveForm.payment_method} onChange={e => setReceiveForm(p => ({ ...p, payment_method: e.target.value }))}><option value="pix">Pix</option><option value="boleto">Boleto</option><option value="cartao">Cartão</option><option value="dinheiro">Dinheiro</option></select>
            <textarea className={areaClass} placeholder="Observações" value={receiveForm.notes} onChange={e => setReceiveForm(p => ({ ...p, notes: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setReceiveModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleReceive}>{processing ? 'Processando...' : 'Confirmar pagamento'}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={adjustModal} onClose={() => { setAdjustModal(false); setModalError(''); }} title="Ajustar cobrança" description="Atualize valor ou vencimento com justificativa obrigatória para manter rastreabilidade." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Novo valor" value={adjustForm.amount} onChange={e => setAdjustForm(p => ({ ...p, amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={adjustForm.due_date} onChange={e => setAdjustForm(p => ({ ...p, due_date: e.target.value }))} />
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Justificativa" value={adjustForm.justification} onChange={e => setAdjustForm(p => ({ ...p, justification: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setAdjustModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleAdjust}>{processing ? 'Salvando...' : 'Salvar ajuste'}</Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={newBillingModal}
        onClose={() => { setNewBillingModal(false); setModalError(''); }}
        title="Nova cobrança avulsa"
        description="Instalação, serviço pontual ou negociação — sem esperar o fechamento mensal. Depois de criar, gere o boleto (Ailos) e a NFS-e pelo detalhe da cobrança."
        size="lg"
      >
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="md:col-span-2">
              <ClientAutocomplete
                clients={clients}
                value={newBillingForm.client_id}
                onChange={(id) => setNewBillingForm(p => ({ ...p, client_id: id }))}
                placeholder="Buscar cliente por nome ou CPF/CNPJ…"
                required
              />
            </div>
            <input className={`${fieldClass} md:col-span-2`} placeholder="Descrição (ex.: INSTALAÇÃO DO RASTREADOR)" value={newBillingForm.title} onChange={e => setNewBillingForm(p => ({ ...p, title: e.target.value }))} />
            <input className={fieldClass} placeholder="Valor (ex.: 120,00)" value={newBillingForm.amount} onChange={e => setNewBillingForm(p => ({ ...p, amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={newBillingForm.due_date} onChange={e => setNewBillingForm(p => ({ ...p, due_date: e.target.value }))} />
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Observações (opcional)" value={newBillingForm.notes} onChange={e => setNewBillingForm(p => ({ ...p, notes: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setNewBillingModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleCreateAvulsa}>{processing ? 'Criando...' : 'Criar cobrança'}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={payableModal} onClose={() => { setPayableModal(false); setModalError(''); }} title="Cadastrar conta a pagar" description="Despesas da empresa: fornecedores, aluguel, chips, impostos…" size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={`${fieldClass} md:col-span-2`} placeholder="Descrição (ex.: Aluguel do galpão)" value={payableForm.description} onChange={e => setPayableForm(p => ({ ...p, description: e.target.value }))} />
            <input className={fieldClass} placeholder="Fornecedor (opcional)" value={payableForm.supplier} onChange={e => setPayableForm(p => ({ ...p, supplier: e.target.value }))} />
            <input className={fieldClass} placeholder="Categoria (ex.: aluguel, chips…)" value={payableForm.category} onChange={e => setPayableForm(p => ({ ...p, category: e.target.value }))} />
            <input className={fieldClass} placeholder="Valor (ex.: 1500,00)" value={payableForm.amount} onChange={e => setPayableForm(p => ({ ...p, amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={payableForm.due_date} onChange={e => setPayableForm(p => ({ ...p, due_date: e.target.value }))} />
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Observações (opcional)" value={payableForm.notes} onChange={e => setPayableForm(p => ({ ...p, notes: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setPayableModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleCreatePayable}>{processing ? 'Salvando...' : 'Cadastrar'}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={!!payingPayable} onClose={() => setPayingPayable(null)} title={payingPayable ? `Pagar — ${payingPayable.description}` : 'Pagar conta'} size="md">
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-2">
            <input type="date" className={fieldClass} value={payPayableForm.payment_date} onChange={e => setPayPayableForm(p => ({ ...p, payment_date: e.target.value }))} />
            <select className={fieldClass} value={payPayableForm.payment_method} onChange={e => setPayPayableForm(p => ({ ...p, payment_method: e.target.value }))}>
              <option value="pix">Pix</option>
              <option value="boleto">Boleto</option>
              <option value="transferencia">Transferência</option>
              <option value="cartao">Cartão</option>
              <option value="dinheiro">Dinheiro</option>
            </select>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setPayingPayable(null)}>Cancelar</button>
            <Button type="button" disabled={processing} onClick={handlePayPayable}>{processing ? 'Salvando...' : 'Confirmar pagamento'}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={batchReceiveModal} onClose={() => setBatchReceiveModal(false)} title={`Receber ${selectedBillingIds.length} cobrança(s) em lote`} description="Todas as selecionadas serão marcadas como pagas com a mesma data e forma." size="md">
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-2">
            <input type="date" className={fieldClass} value={batchReceiveForm.payment_date} onChange={e => setBatchReceiveForm(p => ({ ...p, payment_date: e.target.value }))} />
            <select className={fieldClass} value={batchReceiveForm.payment_method} onChange={e => setBatchReceiveForm(p => ({ ...p, payment_method: e.target.value }))}>
              <option value="pix">Pix</option>
              <option value="boleto">Boleto</option>
              <option value="dinheiro">Dinheiro</option>
              <option value="cartao">Cartão</option>
            </select>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setBatchReceiveModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleBatchReceive}>{processing ? 'Processando...' : 'Receber todas'}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={batchMaintModal} onClose={() => { setBatchMaintModal(false); setModalError(''); }} title={`Manutenção de ${selectedBillingIds.length} título(s) em lote`} description="Aplica novo vencimento e/ou valor a todas as cobranças selecionadas, com justificativa no histórico de cada uma." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input type="date" className={fieldClass} value={batchMaintForm.due_date} onChange={e => setBatchMaintForm(p => ({ ...p, due_date: e.target.value }))} />
            <input className={fieldClass} placeholder="Novo valor (opcional)" value={batchMaintForm.amount} onChange={e => setBatchMaintForm(p => ({ ...p, amount: e.target.value }))} />
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Justificativa (obrigatória)" value={batchMaintForm.justification} onChange={e => setBatchMaintForm(p => ({ ...p, justification: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setBatchMaintModal(false)}>Cancelar</button>
            <Button type="button" disabled={!canEdit || processing} onClick={handleBatchMaint}>{processing ? 'Aplicando...' : 'Aplicar em lote'}</Button>
          </div>
        </div>
      </Modal>

      {/* ── Modal: Enviar boletos via e-mail / WhatsApp ── */}
      <Modal
        open={envioModal}
        onClose={() => { setEnvioModal(false); setEnvioClientId(''); setEnvioCliente(null); setEnvioBillings([]); }}
        title="Enviar boletos via e-mail / WhatsApp"
        description="Escolha o cliente e envie cada boleto em aberto com a mensagem padrão das Configurações."
        size="xl"
      >
        <div className="space-y-4">
          <ClientAutocomplete
            clients={clients}
            value={envioClientId}
            onChange={selecionarClienteEnvio}
            placeholder="Buscar cliente por nome ou CPF/CNPJ…"
          />
          {envioLoading ? (
            <TableSkeleton rows={3} cols={4} />
          ) : envioClientId && envioCliente && envioBillings.length === 0 ? (
            <EmptyState icon={CheckCircle2} title="Nenhum boleto em aberto" description="Este cliente não possui cobranças pendentes ou vencidas." />
          ) : envioBillings.length > 0 ? (
            <Table>
              <TableHead>
                <Th>Nº</Th>
                <Th>Vencimento</Th>
                <Th>Valor</Th>
                <Th>Status</Th>
                <Th className="w-44" />
              </TableHead>
              <TableBody>
                {envioBillings.map(b => (
                  <Tr key={b.id}>
                    <Td className="text-xs text-slate-500">{b.id}</Td>
                    <Td className="text-sm">{formatDate(b.due_date)}</Td>
                    <Td className="font-mono font-semibold">{formatCurrency(b.amount)}</Td>
                    <Td><Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge></Td>
                    <Td>
                      <div className="flex justify-end gap-2">
                        <Button
                          className="px-3 py-1.5 text-xs"
                          onClick={async () => { try { await enviarBoletoWhats(b, envioCliente!, token!); } catch (e) { alert(e instanceof Error ? e.message : 'Erro ao enviar'); } }}
                        >
                          WhatsApp
                        </Button>
                        <Button
                          variant="secondary"
                          className="px-3 py-1.5 text-xs"
                          onClick={async () => { try { await enviarBoletoEmail(b, envioCliente!, token!); } catch (e) { alert(e instanceof Error ? e.message : 'Erro ao enviar'); } }}
                        >
                          E-mail
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </div>
      </Modal>
    </PageShell>
  );
}
