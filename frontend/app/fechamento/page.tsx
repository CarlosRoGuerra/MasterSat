'use client';

import { useEffect, useState } from 'react';
import {
  CalendarCheck, RefreshCw, Download, CheckCircle2, AlertTriangle,
  FileText, DollarSign, ChevronRight, Loader2, Wrench, ClipboardList, Package, Trash2, FileSpreadsheet,
} from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { MetricCard } from '@/components/ui/metric-card';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch, API_URL } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import type { ClientOption } from '@/lib/domain-types';

/* ── Types ─────────────────────────────────────────────────────────────── */
type FilterType = 'all' | 'pf' | 'pj' | 'client';

type FirstMonthCharge = { item_id: number; title: string; amount: number };

type ClosureItem = {
  type: 'recorrente';
  contract_id: number;
  client_id: number;
  client_name: string;
  client_type: string;
  vehicle_plate: string | null;
  tracker_imei: string | null;
  plan_name: string;
  plan_price: number;
  billing_amount: number;
  is_prorata: boolean;
  prorated_days: number;
  days_in_month: number;
  first_month_charges: FirstMonthCharge[];
  total_first_billing: number;
  period_label: string;
  due_date: string;
  already_generated: boolean;
  billing_day: number | null;
};

type UninstallEventItem = {
  type: 'taxa_desinstalacao';
  event_id: number;
  client_id: number;
  client_name: string;
  client_type: string;
  vehicle_plate: string | null;
  uninstall_date: string;
  fee_amount: number;
  deferred: boolean;
  aggregation_total: number;
  skipped: boolean;
  skip_reason: string | null;
};

type ChargeItem = {
  type: 'servico';
  item_id: number;
  client_id: number;
  client_name: string;
  client_type: string;
  vehicle_plate: string | null;
  title: string;
  installment_count: number;
  generated_count: number;
  remaining_installments: number;
  per_installment_amount: number;
  total_remaining: number;
  start_date: string;
};

type Simulation = {
  reference_month: string;
  total_contracts: number;
  to_generate: number;
  already_generated: number;
  total_amount: number;
  items: ClosureItem[];
  uninstall_events: UninstallEventItem[];
  total_uninstall_fees: number;
  charge_items: ChargeItem[];
  total_services: number;
  grand_total: number;
};

type GenerateResult = {
  status: 'completed';
  /** Formato canônico da API (YYYY-MM) — o mesmo aceito nos parâmetros. */
  reference_month: string;
  /** Mesmo mês em formato de exibição (MM/YYYY). */
  reference_month_label: string;
  generated: number;
  total_amount: number;
  uninstall_fees_generated: number;
  uninstall_events_processed: number;
  uninstall_fees_deferred: number;
  uninstall_fees_skipped: number;
  services_generated: number;
  total_services_amount: number;
  grand_total: number;
};

type WizardStep = 1 | 2 | 3;
type GenerateError = { message: string };

/* ── Helpers ───────────────────────────────────────────────────────────── */
function currentYearMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}
function fmt(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}
function fmtDate(iso?: string | null) {
  if (!iso) return '—';
  return new Date(iso.slice(0, 10) + 'T12:00:00').toLocaleDateString('pt-BR');
}

const fieldClass =
  'rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200';

const typeBadge = (type: string) => (
  <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${
    type === 'pj'
      ? 'bg-purple-100 text-purple-700 dark:bg-purple-950/40 dark:text-purple-400'
      : 'bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-400'
  }`}>
    {type === 'pj' ? 'PJ' : 'PF'}
  </span>
);

/* ── Step indicator ─────────────────────────────────────────────────────── */
function StepIndicator({ current }: { current: WizardStep }) {
  const steps = [
    { n: 1, label: 'Escopo' },
    { n: 2, label: 'Preview' },
    { n: 3, label: 'Confirmação' },
  ];
  return (
    <div className="mb-6 flex items-center gap-0">
      {steps.map((s, i) => (
        <div key={s.n} className="flex items-center">
          <div className="flex flex-col items-center">
            <div className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold transition-colors ${
              current > s.n
                ? 'bg-emerald-500 text-white'
                : current === s.n
                ? 'bg-brand-500 text-black'
                : 'bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
            }`}>
              {current > s.n ? <CheckCircle2 className="h-4 w-4" /> : s.n}
            </div>
            <span className={`mt-1 text-[11px] font-medium ${
              current === s.n
                ? 'text-brand-600 dark:text-brand-400'
                : current > s.n
                ? 'text-emerald-600 dark:text-emerald-400'
                : 'text-slate-400 dark:text-slate-500'
            }`}>
              {s.label}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className={`mx-3 mb-5 h-0.5 w-16 transition-colors ${
              current > s.n ? 'bg-emerald-400' : 'bg-slate-200 dark:bg-slate-700'
            }`} />
          )}
        </div>
      ))}
    </div>
  );
}

/* ── Main page ─────────────────────────────────────────────────────────── */
export default function FechamentoPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(
    ROUTE_ROLES['/fechamento'],
    '/login/admin',
  );

  const [step, setStep] = useState<WizardStep>(1);

  // Step 1
  const [referenceMonth, setReferenceMonth] = useState(currentYearMonth);
  const [filterType, setFilterType] = useState<FilterType>('all');
  const [selectedClientId, setSelectedClientId] = useState('');
  const [clients, setClients] = useState<ClientOption[]>([]);

  // Step 2
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [showAlready, setShowAlready] = useState(false);
  const [selectedContractIds, setSelectedContractIds] = useState<Set<number>>(new Set());

  // Step 3
  const [generateResult, setGenerateResult] = useState<GenerateResult | null>(null);
  const [generating, setGenerating] = useState(false);

  const [error, setError] = useState('');

  useEffect(() => {
    if (!token) return;
    apiFetch<ClientOption[]>('/clients?limit=300', {}, token)
      .then(setClients)
      .catch(() => null);
  }, [token]);


  // Derived lists
  const recorrentes = simulation?.items ?? [];
  const desinstalacoes = simulation?.uninstall_events ?? [];
  const chargeItems = simulation?.charge_items ?? [];
  const displayedRec = showAlready ? recorrentes : recorrentes.filter((i) => !i.already_generated);
  const pagRec = usePagination(displayedRec, 50);
  const pagDes = usePagination(desinstalacoes, 50);
  const pagSvc = usePagination(chargeItems, 50);
  useEffect(() => { pagRec.reset(); }, [displayedRec.length]);
  useEffect(() => { pagDes.reset(); }, [desinstalacoes.length]);
  useEffect(() => { pagSvc.reset(); }, [chargeItems.length]);

  async function refreshSimulation() {
    if (!token || !referenceMonth) return;
    try {
      const params = new URLSearchParams({ reference_month: referenceMonth, filter_type: filterType });
      if (filterType === 'client' && selectedClientId) params.set('client_id', selectedClientId);
      const data = await apiFetch<Simulation>(`/billing-closure/simulate?${params}`, {}, token);
      setSimulation(data);
      // Keep previously selected IDs that are still pending; add new ones
      setSelectedContractIds(prev => {
        const stillPending = new Set(data.items.filter(i => !i.already_generated).map(i => i.contract_id));
        return new Set([...prev].filter(id => stillPending.has(id)));
      });
    } catch { /* silent */ }
  }

  async function runSimulation() {
    if (!token || !referenceMonth) return;
    setSimLoading(true);
    setError('');
    setSimulation(null);
    try {
      const params = new URLSearchParams({ reference_month: referenceMonth, filter_type: filterType });
      if (filterType === 'client' && selectedClientId) params.set('client_id', selectedClientId);
      const data = await apiFetch<Simulation>(`/billing-closure/simulate?${params}`, {}, token);
      setSimulation(data);
      // Pre-select all non-generated contracts
      setSelectedContractIds(new Set(data.items.filter(i => !i.already_generated).map(i => i.contract_id)));
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao simular fechamento.');
    } finally {
      setSimLoading(false);
    }
  }

  async function downloadPdf() {
    if (!token) return;
    const params = new URLSearchParams({ reference_month: referenceMonth, filter_type: filterType });
    if (filterType === 'client' && selectedClientId) params.set('client_id', selectedClientId);
    try {
      const resp = await fetch(`${API_URL}/billing-closure/simulate/pdf?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error('Erro ao gerar PDF');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `fechamento-${referenceMonth}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('Não foi possível gerar o PDF. Tente novamente.');
    }
  }

  async function downloadXlsx() {
    if (!token) return;
    const params = new URLSearchParams({ reference_month: referenceMonth, filter_type: filterType });
    if (filterType === 'client' && selectedClientId) params.set('client_id', selectedClientId);
    try {
      const resp = await fetch(`${API_URL}/billing-closure/simulate/xlsx?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error('Erro ao gerar Excel');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `fechamento-${referenceMonth}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('Não foi possível gerar o Excel. Tente novamente.');
    }
  }

  async function confirmGenerate() {
    if (!token || !simulation || !hasWork) return;
    setGenerating(true);
    setError('');
    setGenerateResult(null);
    setStep(3);
    try {
      const params = new URLSearchParams({ reference_month: referenceMonth, filter_type: filterType });
      if (filterType === 'client' && selectedClientId) params.set('client_id', selectedClientId);
      // Manda SEMPRE a seleção exata das mensalidades, mesmo quando todas estão
      // marcadas. Ausência de contract_ids significa "estado atual do banco";
      // um contrato criado entre Simular e Gerar entraria sem ter aparecido na
      // prévia. O sentinela -1 representa uma seleção intencionalmente vazia.
      selectedContractIds.forEach(id => params.append('contract_ids', String(id)));
      if (selectedContractIds.size === 0) params.append('contract_ids', '-1');
      // A prévia é um snapshot: envia também as seleções exatas de taxas e
      // serviços. Assim um lançamento criado por outra pessoa entre Simular e
      // Gerar não entra silenciosamente nesta execução.
      const eventIds = desinstalacoes.filter(e => !e.deferred).map(e => e.event_id);
      eventIds.forEach(id => params.append('uninstall_event_ids', String(id)));
      if (eventIds.length === 0) params.append('uninstall_event_ids', '-1');
      chargeItems.forEach(item => params.append('charge_item_ids', String(item.item_id)));
      if (chargeItems.length === 0) params.append('charge_item_ids', '-1');
      const result = await apiFetch<GenerateResult>(`/billing-closure/generate?${params}`, { method: 'POST' }, token);
      setGenerateResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao executar fechamento.');
      setStep(2);
    } finally {
      setGenerating(false);
    }
  }

  function restart() {
    setStep(1);
    setSimulation(null);
    setGenerateResult(null);
    setSelectedContractIds(new Set());
    setError('');
    setGenerating(false);
  }

  // Selection helpers
  const pendingRec = recorrentes.filter(i => !i.already_generated);
  const allSelected = pendingRec.length > 0 && pendingRec.every(i => selectedContractIds.has(i.contract_id));
  const someSelected = pendingRec.some(i => selectedContractIds.has(i.contract_id));

  function toggleSelectAll() {
    if (allSelected) {
      setSelectedContractIds(new Set());
    } else {
      setSelectedContractIds(new Set(pendingRec.map(i => i.contract_id)));
    }
  }

  function toggleContract(id: number) {
    setSelectedContractIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const hasWork =
    simulation &&
    (selectedContractIds.size > 0 ||
      desinstalacoes.filter((e) => !e.deferred).length > 0 ||
      chargeItems.length > 0);

  /* ── Confirm button label ── */
  function confirmLabel() {
    if (!simulation) return 'Nada a gerar';
    const parts: string[] = [];
    if (selectedContractIds.size > 0) parts.push(`${selectedContractIds.size} mensalidade(s)`);
    const taxas = desinstalacoes.filter((e) => !e.deferred).length;
    if (taxas > 0) parts.push(`${taxas} taxa(s)`);
    if (chargeItems.length > 0) parts.push(`${chargeItems.length} serviço(s)`);
    return parts.length ? `Confirmar e gerar: ${parts.join(' + ')}` : 'Nada a gerar';
  }

  return (
    <PageShell
      title="Fechamento"
      description="Wizard de geração em lote das cobranças mensais, taxas de desinstalação e serviços."
    >
      <StepIndicator current={step} />

      {(guardError || error) && (
        <div className="mb-4">
          <ErrorBanner message={guardError || error} />
        </div>
      )}

      {/* ════════════════════════════════════════
          STEP 1 — Escopo
      ════════════════════════════════════════ */}
      {step === 1 && (
        <Card>
          <h2 className="mb-4 text-base font-semibold text-slate-800 dark:text-white">
            1. Defina o escopo do fechamento
          </h2>
          <div className="flex flex-wrap items-end gap-5">
            <div>
              <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Mês de referência</p>
              <input
                type="month"
                className={fieldClass}
                value={referenceMonth}
                onChange={(e) => setReferenceMonth(e.target.value)}
              />
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Grupo de clientes</p>
              <div className="flex gap-1.5">
                {(['all', 'pf', 'pj', 'client'] as FilterType[]).map((ft) => (
                  <button
                    key={ft}
                    type="button"
                    onClick={() => { setFilterType(ft); setSelectedClientId(''); }}
                    className={`rounded-lg border px-3 py-2 text-xs font-semibold transition-colors ${
                      filterType === ft
                        ? 'border-brand-500 bg-brand-500 text-black'
                        : 'border-slate-200 bg-white text-slate-600 hover:border-brand-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
                    }`}
                  >
                    {ft === 'all' ? 'Todos' : ft === 'pf' ? 'Pessoa Física' : ft === 'pj' ? 'Pessoa Jurídica' : 'Individual'}
                  </button>
                ))}
              </div>
            </div>

            {filterType === 'client' && (
              <div className="min-w-[240px]">
                <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Cliente</p>
                <ClientAutocomplete
                  clients={clients}
                  value={selectedClientId}
                  onChange={setSelectedClientId}
                />
              </div>
            )}
          </div>

          <div className="mt-6 flex justify-end">
            <Button
              onClick={runSimulation}
              disabled={simLoading || guardLoading || (filterType === 'client' && !selectedClientId)}
              className="gap-2"
            >
              {simLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {simLoading ? 'Simulando...' : 'Simular fechamento'}
              {!simLoading && <ChevronRight className="h-4 w-4" />}
            </Button>
          </div>
        </Card>
      )}

      {/* ════════════════════════════════════════
          STEP 2 — Preview
      ════════════════════════════════════════ */}
      {step === 2 && simulation && (
        <>
          {/* KPIs */}
          <section className="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <MetricCard
              label="Contratos no período"
              value={simulation.total_contracts}
              sub={`período ${simulation.reference_month}`}
              icon={<FileText className="h-5 w-5" />}
            />
            <MetricCard
              label="Mensalidades a gerar"
              value={
                <span className={simulation.to_generate > 0 ? 'text-amber-600 dark:text-amber-400' : ''}>
                  {simulation.to_generate}
                </span>
              }
              sub={(() => {
                const hasProrata = simulation.items.some(i => i.is_prorata);
                const hasCombined = simulation.items.some(i => i.first_month_charges?.length > 0);
                const tags = [hasProrata && 'pró-ratas', hasCombined && 'serv. iniciais'].filter(Boolean);
                return `${fmt(simulation.total_amount)}${tags.length ? ` (incl. ${tags.join(' + ')})` : ''}`;
              })()}
              icon={<AlertTriangle className="h-5 w-5" />}
            />
            <MetricCard
              label="Taxas desinstalação"
              value={
                <span className={desinstalacoes.filter(e => !e.deferred).length > 0 ? 'text-orange-600 dark:text-orange-400' : ''}>
                  {desinstalacoes.filter(e => !e.deferred).length}
                </span>
              }
              sub={`${fmt(simulation.total_uninstall_fees)} a faturar`}
              icon={<Wrench className="h-5 w-5" />}
            />
            <MetricCard
              label="Serviços / cobranças"
              value={
                <span className={chargeItems.length > 0 ? 'text-blue-600 dark:text-blue-400' : ''}>
                  {chargeItems.length}
                </span>
              }
              sub={`${fmt(simulation.total_services)} a gerar`}
              icon={<Package className="h-5 w-5" />}
            />
            <MetricCard
              label="Total geral"
              value={fmt(simulation.grand_total)}
              sub="mensalidades + taxas + serviços"
              icon={<DollarSign className="h-5 w-5" />}
            />
          </section>

          {/* Mensalidades */}
          <Card className="mb-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                <ClipboardList className="h-4 w-4 text-brand-500" />
                Mensalidades recorrentes ({recorrentes.length})
              </h3>
              <div className="flex items-center gap-4">
                <label className="flex cursor-pointer select-none items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                  <input
                    type="checkbox"
                    checked={showAlready}
                    onChange={(e) => setShowAlready(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  Mostrar já geradas
                </label>
                {pendingRec.length > 0 && (
                  <label className="flex cursor-pointer select-none items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => { if (el) el.indeterminate = !allSelected && someSelected; }}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-300"
                    />
                    {allSelected ? 'Desmarcar todos' : 'Selecionar todos'}
                  </label>
                )}
              </div>
              <span className="text-xs text-slate-400">
                {selectedContractIds.size} de {pendingRec.length} selecionados
              </span>
            </div>

              {displayedRec.length === 0 ? (
                <EmptyState
                  icon={CalendarCheck}
                  title="Nenhuma cobrança a gerar"
                  description="Todos os contratos deste período já foram faturados."
                />
              ) : (
                <>
                  <Table>
                    <TableHead>
                      <Th className="w-10" />
                      <Th>Cliente</Th>
                      <Th>Tipo</Th>
                      <Th>Veículo</Th>
                      <Th>Plano</Th>
                      <Th>Vencimento</Th>
                      <Th>Composição da cobrança</Th>
                      <Th>Valor a cobrar</Th>
                      <Th>Status</Th>
                      <Th className="w-10" />
                    </TableHead>
                    <TableBody>
                      {pagRec.slice.map((item) => {
                        const hasFirstCharges = item.first_month_charges?.length > 0;
                        return (
                        <Tr key={item.contract_id} className={!item.already_generated && !selectedContractIds.has(item.contract_id) ? 'opacity-40' : ''}>
                          <Td>
                            {!item.already_generated && (
                              <input
                                type="checkbox"
                                checked={selectedContractIds.has(item.contract_id)}
                                onChange={() => toggleContract(item.contract_id)}
                                className="rounded border-slate-300"
                              />
                            )}
                          </Td>
                          <Td>
                            <p className="font-medium text-slate-900 dark:text-white">{item.client_name}</p>
                          </Td>
                          <Td>{typeBadge(item.client_type)}</Td>
                          <Td>
                            <p className="text-sm text-slate-700 dark:text-slate-300">{item.vehicle_plate ?? '—'}</p>
                            {item.tracker_imei && (
                              <p className="text-[11px] text-slate-400">{item.tracker_imei}</p>
                            )}
                          </Td>
                          <Td className="text-sm text-slate-700 dark:text-slate-300">{item.plan_name}</Td>
                          <Td className="whitespace-nowrap text-sm tabular-nums text-slate-700 dark:text-slate-300">
                            {fmtDate(item.due_date)}
                          </Td>
                          <Td>
                            {hasFirstCharges ? (
                              <span className="inline-flex flex-col gap-0.5">
                                <span className="inline-flex items-center gap-1 rounded-md bg-violet-100 px-2 py-0.5 text-xs font-semibold text-violet-700 dark:bg-violet-950/40 dark:text-violet-400">
                                  1ª cobrança combinada
                                </span>
                                <span className="text-[11px] text-slate-500 dark:text-slate-400">
                                  Mensalidade: {fmt(item.billing_amount)}
                                </span>
                                {item.first_month_charges.map((c) => (
                                  <span key={c.item_id} className="inline-flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
                                    + {c.title}: {fmt(c.amount)}
                                    <button
                                      type="button"
                                      title="Remover este lançamento"
                                      onClick={async () => {
                                        if (!token) return;
                                        try {
                                          await apiFetch(`/client-charge-items/${c.item_id}`, { method: 'DELETE' }, token);
                                          await refreshSimulation();
                                        } catch { /* silent */ }
                                      }}
                                      className="ml-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-rose-100 text-rose-500 hover:bg-rose-200 dark:bg-rose-950/40 dark:text-rose-400"
                                    >
                                      ×
                                    </button>
                                  </span>
                                ))}
                              </span>
                            ) : item.is_prorata ? (
                              <span className="inline-flex flex-col">
                                <span className="inline-flex items-center rounded-md bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
                                  Pró-rata {item.prorated_days}/{item.days_in_month} dias
                                </span>
                                <span className="mt-0.5 text-[10px] text-slate-400 line-through">
                                  {fmt(item.plan_price)}
                                </span>
                              </span>
                            ) : (
                              <span className="text-xs text-slate-400">Mensalidade integral</span>
                            )}
                          </Td>
                          <Td className="whitespace-nowrap text-sm font-semibold text-slate-900 dark:text-white">
                            {fmt(item.total_first_billing ?? item.billing_amount)}
                          </Td>
                          <Td>
                            {item.already_generated ? (
                              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                Gerado
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
                                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                                A gerar
                              </span>
                            )}
                          </Td>
                          <Td>
                            {!item.already_generated && (
                              <button
                                type="button"
                                title="Excluir contrato e lançamentos"
                                onClick={async () => {
                                  if (!token) return;
                                  if (!window.confirm('Excluir este contrato e todos os lançamentos associados? Esta ação não pode ser desfeita.')) return;
                                  try {
                                    // Delete all associated charge items first
                                    for (const c of item.first_month_charges ?? []) {
                                      await apiFetch(`/client-charge-items/${c.item_id}`, { method: 'DELETE' }, token);
                                    }
                                    // Cancel the contract
                                    await apiFetch(`/contracts/${item.contract_id}`, { method: 'DELETE' }, token);
                                    await refreshSimulation();
                                  } catch { /* silent */ }
                                }}
                                className="flex h-7 w-7 items-center justify-center rounded-lg border border-rose-200 bg-rose-50 text-rose-500 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400 dark:hover:bg-rose-950/50"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </Td>
                        </Tr>
                        );
                      })}
                    </TableBody>
                  </Table>
                  {displayedRec.length > 0 && (
                    <Pagination
                      page={pagRec.page}
                      totalPages={pagRec.totalPages}
                      total={pagRec.total}
                      start={pagRec.start}
                      end={pagRec.end}
                      onPage={pagRec.setPage}
                      className="mt-4"
                    />
                  )}
                </>
              )}
          </Card>

          {/* Desinstalações */}
          {desinstalacoes.length > 0 && (
            <Card className="mb-5">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                <Wrench className="h-4 w-4 text-orange-500" />
                Taxas de desinstalação ({desinstalacoes.length})
              </h3>
              {(
                <>
                  <Table>
                    <TableHead>
                      <Th>Cliente</Th>
                      <Th>Tipo</Th>
                      <Th>Veículo</Th>
                      <Th>Data de retirada</Th>
                      <Th>Valor da taxa</Th>
                      <Th>Status</Th>
                    </TableHead>
                    <TableBody>
                      {pagDes.slice.map((item) => (
                        <Tr key={item.event_id}>
                          <Td>
                            <p className="font-medium text-slate-900 dark:text-white">{item.client_name}</p>
                          </Td>
                          <Td>{typeBadge(item.client_type)}</Td>
                          <Td className="text-sm text-slate-700 dark:text-slate-300">{item.vehicle_plate ?? '—'}</Td>
                          <Td className="whitespace-nowrap text-sm tabular-nums text-slate-700 dark:text-slate-300">
                            {fmtDate(item.uninstall_date)}
                          </Td>
                          <Td className="whitespace-nowrap text-sm font-semibold text-slate-900 dark:text-white">
                            {fmt(item.fee_amount)}
                          </Td>
                          <Td>
                            {item.deferred ? (
                              <span
                                className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400"
                                title={item.skip_reason ?? ''}
                              >
                                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                                Aguardando acumulação
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-orange-700 dark:text-orange-400">
                                <span className="h-1.5 w-1.5 rounded-full bg-orange-500" />
                                A faturar
                              </span>
                            )}
                          </Td>
                        </Tr>
                      ))}
                    </TableBody>
                  </Table>
                  {desinstalacoes.length > 0 && (
                    <Pagination
                      page={pagDes.page}
                      totalPages={pagDes.totalPages}
                      total={pagDes.total}
                      start={pagDes.start}
                      end={pagDes.end}
                      onPage={pagDes.setPage}
                      className="mt-4"
                    />
                  )}
                </>
              )}
            </Card>
          )}

          {/* Serviços */}
          {chargeItems.length > 0 && (
            <Card className="mb-5">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                <Package className="h-4 w-4 text-blue-500" />
                Serviços e cobranças avulsas ({chargeItems.length})
              </h3>
              {(
                <>
                  <Table>
                    <TableHead>
                      <Th>Cliente</Th>
                      <Th>Tipo</Th>
                      <Th>Veículo</Th>
                      <Th>Serviço / título</Th>
                      <Th>Parcelas pendentes</Th>
                      <Th>Valor/parcela</Th>
                      <Th>Total restante</Th>
                    </TableHead>
                    <TableBody>
                      {pagSvc.slice.map((item) => (
                        <Tr key={item.item_id}>
                          <Td>
                            <p className="font-medium text-slate-900 dark:text-white">{item.client_name}</p>
                          </Td>
                          <Td>{typeBadge(item.client_type)}</Td>
                          <Td className="text-sm text-slate-700 dark:text-slate-300">{item.vehicle_plate ?? '—'}</Td>
                          <Td>
                            <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{item.title}</p>
                            <p className="text-[11px] text-slate-400">
                              início {fmtDate(item.start_date)}
                            </p>
                          </Td>
                          <Td>
                            <span className="inline-flex items-center rounded-md bg-blue-100 px-2 py-0.5 text-xs font-semibold text-blue-700 dark:bg-blue-950/40 dark:text-blue-400">
                              {item.remaining_installments} de {item.installment_count}
                            </span>
                          </Td>
                          <Td className="whitespace-nowrap text-sm tabular-nums text-slate-700 dark:text-slate-300">
                            {fmt(item.per_installment_amount)}
                          </Td>
                          <Td className="whitespace-nowrap text-sm font-semibold text-slate-900 dark:text-white">
                            {fmt(item.total_remaining)}
                          </Td>
                        </Tr>
                      ))}
                    </TableBody>
                  </Table>
                  {chargeItems.length > 0 && (
                    <Pagination
                      page={pagSvc.page}
                      totalPages={pagSvc.totalPages}
                      total={pagSvc.total}
                      start={pagSvc.start}
                      end={pagSvc.end}
                      onPage={pagSvc.setPage}
                      className="mt-4"
                    />
                  )}
                </>
              )}
            </Card>
          )}

          {/* Actions */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Button variant="secondary" onClick={() => setStep(1)} className="gap-2">
              Alterar escopo
            </Button>
            <div className="flex gap-3">
              <Button variant="secondary" onClick={downloadPdf} className="gap-2">
                <Download className="h-4 w-4" />
                PDF de conferência
              </Button>
              <Button variant="secondary" onClick={downloadXlsx} className="gap-2">
                <FileSpreadsheet className="h-4 w-4" />
                Excel
              </Button>
              <Button
                onClick={confirmGenerate}
                disabled={generating || !hasWork}
                className="gap-2"
              >
                <CalendarCheck className="h-4 w-4" />
                {hasWork ? confirmLabel() : 'Nada a gerar'}
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {/* ════════════════════════════════════════
          STEP 3 — Resultado do fechamento
      ════════════════════════════════════════ */}
      {step === 3 && (
        <Card>
          <h2 className="mb-5 text-base font-semibold text-slate-800 dark:text-white">
            3. Resultado do fechamento
          </h2>

          {generating && (
            <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-5 dark:bg-slate-800/40">
              <Loader2 className="h-5 w-5 animate-spin text-brand-500" />
              <div>
                <p className="text-sm font-medium text-slate-700 dark:text-slate-200">
                  Gerando cobranças, aguarde...
                </p>
                <p className="text-xs text-slate-400">Este processo pode levar alguns segundos</p>
              </div>
            </div>
          )}

          {generateResult && (
            <div className="space-y-5">
              <span className="inline-flex items-center gap-2 rounded-full bg-emerald-100 px-3 py-1.5 text-sm font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Fechamento concluído — {generateResult.reference_month_label || generateResult.reference_month}
              </span>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label="Mensalidades geradas"
                  value={generateResult.generated}
                  sub="cobranças recorrentes + pró-ratas"
                  icon={<ClipboardList className="h-5 w-5" />}
                />
                <MetricCard
                  label="Eventos de desinstalação"
                  value={generateResult.uninstall_events_processed}
                  sub={`${generateResult.uninstall_fees_generated} cobrança(s) · ${generateResult.uninstall_fees_deferred} aguardando acumulação`}
                  icon={<Wrench className="h-5 w-5" />}
                />
                <MetricCard
                  label="Serviços gerados"
                  value={generateResult.services_generated}
                  sub={fmt(generateResult.total_services_amount)}
                  icon={<Package className="h-5 w-5" />}
                />
                <MetricCard
                  label="Total faturado"
                  value={fmt(generateResult.grand_total)}
                  sub="mensalidades + taxas + serviços"
                  icon={<DollarSign className="h-5 w-5" />}
                />
              </div>
            </div>
          )}

          <div className="mt-6 flex gap-3">
            {!generating && (
              <Button onClick={restart} className="gap-2">
                <RefreshCw className="h-4 w-4" />
                Novo fechamento
              </Button>
            )}
          </div>
        </Card>
      )}
    </PageShell>
  );
}
