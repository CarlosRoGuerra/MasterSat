'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  Users, Car, Radio, ClipboardList, Wallet,
  AlertTriangle, TrendingUp, ArrowRight, CalendarClock, CheckCircle2,
} from 'lucide-react';
import { BarChart } from '@/components/ui/bar-chart';
import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Skeleton } from '@/components/ui/empty-state';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

/* ── Types ─────────────────────────────────────────────────────────────── */
type UpcomingBilling = {
  id: number; client_name: string;
  amount: number; due_date: string; days_until: number;
};

type DashboardData = {
  clients: {
    active: number; inactive: number; delinquent: number;
    new_this_month: number; new_prev_month: number;
  };
  vehicles: { total: number };
  trackers: { installed: number; stock: number; maintenance: number };
  service_orders: { open: number; in_progress: number; completed: number };
  finance: {
    pending_count: number; overdue_count: number;
    received_month: number; received_prev_month: number;
    delta_received: number; delta_pct: number;
  };
  upcoming_billings: UpcomingBilling[];
};

type DelinquencyStatus = {
  clientes_inadimplentes: number;
  cobrancas_vencidas: number;
  valor_total_vencido: number;
};

/* ── Helpers ───────────────────────────────────────────────────────────── */
function currency(v?: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v ?? 0);
}

function fmtDate(iso: string) {
  const [y, m, d] = iso.split('-');
  return `${d}/${m}/${y}`;
}

/* ── Sub-components ─────────────────────────────────────────────────────── */
function ProgressBar({ value, tone = 'brand' }: { value: number; tone?: 'brand' | 'success' | 'warning' | 'danger' }) {
  const bar = { brand: 'bg-brand-500', success: 'bg-emerald-500', warning: 'bg-amber-500', danger: 'bg-rose-500' }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
      <div className={`h-1.5 rounded-full transition-all ${bar}`} style={{ width: `${Math.min(Math.max(value, 0), 100)}%` }} />
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────── */
export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [delinquency, setDelinquency] = useState<DelinquencyStatus | null>(null);
  const [error, setError] = useState('');
  const { token, loading, error: guardError } = useAuthGuard(
    ['admin', 'operacional', 'financeiro'],
    '/login/admin',
  );

  useEffect(() => {
    if (!token) return;
    apiFetch<DashboardData>('/dashboard', {}, token)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Não foi possível carregar o dashboard.'));
    apiFetch<DelinquencyStatus>('/delinquency/status', {}, token)
      .then(setDelinquency)
      .catch(() => null);
  }, [token]);

  /* ── Derived metrics ── */
  const totals = useMemo(() => {
    if (!data) return { clientTotal: 0, clientHealthy: 0, osCompletion: 0, osTotal: 0, financeRisk: 0, finTotal: 0 };
    const clientTotal = data.clients.active + data.clients.inactive + data.clients.delinquent;
    const osTotal = data.service_orders.open + data.service_orders.in_progress + data.service_orders.completed;
    const finTotal = data.finance.pending_count + data.finance.overdue_count;
    return {
      clientTotal,
      clientHealthy: clientTotal >= 5 ? Math.round((data.clients.active / clientTotal) * 100) : null,
      osCompletion: osTotal >= 3 ? Math.round((data.service_orders.completed / osTotal) * 100) : null,
      osTotal,
      financeRisk: finTotal >= 2 ? Math.round((data.finance.overdue_count / finTotal) * 100) : null,
      finTotal,
    };
  }, [data]);

  const labelClass = 'text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 dark:text-slate-500';

  return (
    <PageShell title="Dashboard" description="Visão executiva da operação — cadastros, campo e financeiro.">

      {(guardError || error) && (
        <div className="mb-4"><ErrorBanner message={guardError || error} /></div>
      )}

      {/* ── Alerta de inadimplência ─────────────────────────────────────── */}
      {delinquency && delinquency.clientes_inadimplentes > 0 && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 dark:border-rose-800/50 dark:bg-rose-950/20">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400" />
          <div className="flex-1 text-sm">
            <span className="font-semibold text-rose-800 dark:text-rose-300">
              {delinquency.clientes_inadimplentes} cliente(s) inadimplente(s)
            </span>
            <span className="ml-2 text-rose-600 dark:text-rose-400">
              · {delinquency.cobrancas_vencidas} cobrança(s) · {currency(delinquency.valor_total_vencido)} em aberto
            </span>
          </div>
          <Link href="/relatorios" className="shrink-0 rounded-lg border border-rose-200 bg-white px-3 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:bg-transparent dark:text-rose-400">
            Ver relatório →
          </Link>
        </div>
      )}

      {/* ── KPI row ─────────────────────────────────────────────────────── */}
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900">
              <Skeleton className="mb-3 h-3 w-28" />
              <Skeleton className="h-8 w-16" />
              <Skeleton className="mt-2 h-3 w-36" />
            </div>
          ))
        ) : (
          <>
            {/* Clientes ativos — info neutro, azul */}
            <StatCard
              label="Clientes ativos"
              value={data?.clients.active ?? '—'}
              hint="Base ativa monitorada"
              tone="default"
              icon={<Users className="h-5 w-5" />}
              delta={data ? {
                value: data.clients.new_this_month - data.clients.new_prev_month,
              } : undefined}
            />

            {/* Rastreadores instalados — saúde operacional, verde */}
            <StatCard
              label="Rastreadores instalados"
              value={data?.trackers.installed ?? '—'}
              hint="Equipamentos em produção"
              tone="success"
              icon={<Radio className="h-5 w-5" />}
            />

            {/* OS em andamento — neutro operacional */}
            <StatCard
              label="OS em andamento"
              value={data?.service_orders.in_progress ?? '—'}
              hint={`${data?.service_orders.open ?? 0} aguardando início`}
              tone={data && data.service_orders.open > 5 ? 'warning' : 'default'}
              icon={<ClipboardList className="h-5 w-5" />}
            />

            {/* Receita do mês — saúde financeira */}
            <StatCard
              label="Receita do mês"
              value={currency(data?.finance.received_month)}
              hint="Pagamentos confirmados"
              tone={data && data.finance.overdue_count > 0 ? 'warning' : 'success'}
              icon={<Wallet className="h-5 w-5" />}
              delta={data ? {
                value: Math.round(data.finance.delta_received * 100) / 100,
                pct: data.finance.delta_pct,
              } : undefined}
            />
          </>
        )}
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_360px]">

        {/* ── Left column ─────────────────────────────────────────────── */}
        <div className="space-y-6">

          {/* Alertas inline */}
          {data && (data.finance.overdue_count > 0 || data.service_orders.open > 0) && (
            <Card>
              <SectionHeader eyebrow="Alertas" title="Itens que precisam de atenção" />
              <div className="mt-4 space-y-2">
                {data.finance.overdue_count > 0 && (
                  <Link href="/financeiro" className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm transition hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                    <span className="text-amber-800 dark:text-amber-300">
                      <strong>{data.finance.overdue_count}</strong> cobrança(s) vencida(s) sem baixa
                    </span>
                    <ArrowRight className="ml-auto h-4 w-4 text-amber-400" />
                  </Link>
                )}
                {data.service_orders.open > 0 && (
                  <Link href="/ordens-servico" className="flex items-center gap-3 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm transition hover:bg-sky-100 dark:border-sky-900/40 dark:bg-sky-950/30">
                    <ClipboardList className="h-4 w-4 shrink-0 text-sky-600 dark:text-sky-400" />
                    <span className="text-sky-800 dark:text-sky-300">
                      <strong>{data.service_orders.open}</strong> OS aberta(s) sem andamento
                    </span>
                    <ArrowRight className="ml-auto h-4 w-4 text-sky-400" />
                  </Link>
                )}
              </div>
            </Card>
          )}

          {/* Saúde da operação */}
          <Card>
            <SectionHeader
              eyebrow="Indicadores"
              title="Saúde da operação"
              description="Proporção de clientes ativos, conclusão de OS e risco de inadimplência."
            />
            <div className="mt-5 grid gap-4 md:grid-cols-3">
              {/* Clientes ativos */}
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center justify-between">
                  <p className={labelClass}>Clientes ativos</p>
                  {totals.clientHealthy !== null ? (
                    <span className="text-lg font-bold text-slate-900 dark:text-white">{totals.clientHealthy}%</span>
                  ) : (
                    <span className="text-xs font-medium text-slate-400">{data?.clients.active ?? 0} total</span>
                  )}
                </div>
                {totals.clientHealthy !== null ? (
                  <>
                    <div className="mt-3"><ProgressBar value={totals.clientHealthy} tone="success" /></div>
                    <p className="mt-2 text-[12px] text-slate-400">{data?.clients.active ?? 0} de {totals.clientTotal}</p>
                  </>
                ) : (
                  <p className="mt-3 text-[12px] text-slate-400">
                    {data?.clients.active === 0 ? 'Nenhum cliente ativo ainda' : 'Todos ativos'}
                  </p>
                )}
                <Link href="/clientes" className="mt-3 flex items-center gap-1 text-[11px] font-semibold text-brand-600 hover:underline dark:text-brand-400">
                  Ver clientes <ArrowRight className="h-3 w-3" />
                </Link>
              </div>

              {/* Conclusão de OS */}
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center justify-between">
                  <p className={labelClass}>Conclusão de OS</p>
                  {totals.osCompletion !== null ? (
                    <span className="text-lg font-bold text-slate-900 dark:text-white">{totals.osCompletion}%</span>
                  ) : (
                    <span className="text-xs font-medium text-slate-400">{totals.osTotal} total</span>
                  )}
                </div>
                {totals.osCompletion !== null ? (
                  <>
                    <div className="mt-3"><ProgressBar value={totals.osCompletion} tone="brand" /></div>
                    <p className="mt-2 text-[12px] text-slate-400">{data?.service_orders.completed ?? 0} concluídas de {totals.osTotal}</p>
                  </>
                ) : (
                  <p className="mt-3 text-[12px] text-slate-400">
                    {totals.osTotal === 0 ? 'Nenhuma OS criada ainda' : `${data?.service_orders.completed ?? 0} concluída(s)`}
                  </p>
                )}
                <Link href="/ordens-servico" className="mt-3 flex items-center gap-1 text-[11px] font-semibold text-brand-600 hover:underline dark:text-brand-400">
                  Ver ordens <ArrowRight className="h-3 w-3" />
                </Link>
              </div>

              {/* Risco financeiro */}
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center justify-between">
                  <p className={labelClass}>Risco financeiro</p>
                  {totals.financeRisk !== null ? (
                    <span className={`text-lg font-bold ${totals.financeRisk > 20 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-900 dark:text-white'}`}>
                      {totals.financeRisk}%
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                      <CheckCircle2 className="h-3 w-3" /> OK
                    </span>
                  )}
                </div>
                {totals.financeRisk !== null ? (
                  <>
                    <div className="mt-3"><ProgressBar value={totals.financeRisk} tone={totals.financeRisk > 20 ? 'danger' : 'warning'} /></div>
                    <p className="mt-2 text-[12px] text-slate-400">{data?.finance.overdue_count ?? 0} vencidas de {totals.finTotal}</p>
                  </>
                ) : (
                  <p className="mt-3 text-[12px] text-slate-400">
                    {data?.finance.overdue_count === 0 ? 'Sem cobranças vencidas' : `${data?.finance.overdue_count} vencida(s)`}
                  </p>
                )}
                <Link href="/relatorios" className="mt-3 flex items-center gap-1 text-[11px] font-semibold text-brand-600 hover:underline dark:text-brand-400">
                  Ver relatório <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </div>
          </Card>

          {/* Indicadores por módulo */}
          <Card>
            <SectionHeader eyebrow="Módulos" title="Indicadores por módulo" />
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { title: 'Clientes', href: '/clientes', rows: [
                    { label: 'Ativos',        v: data?.clients.active },
                    { label: 'Inativos',      v: data?.clients.inactive },
                    { label: 'Inadimplentes', v: data?.clients.delinquent, danger: (data?.clients.delinquent ?? 0) > 0 },
                  ]},
                { title: 'Veículos', href: '/veiculos', rows: [
                    { label: 'Total',           v: data?.vehicles.total },
                    { label: 'Com rastreador',  v: data?.trackers.installed },
                    { label: 'Sem rastreador',  v: Math.max((data?.vehicles.total ?? 0) - (data?.trackers.installed ?? 0), 0) },
                  ]},
                { title: 'Ordens de serviço', href: '/ordens-servico', rows: [
                    { label: 'Abertas',      v: data?.service_orders.open, warn: (data?.service_orders.open ?? 0) > 0 },
                    { label: 'Em andamento', v: data?.service_orders.in_progress },
                    { label: 'Concluídas',   v: data?.service_orders.completed },
                  ]},
                { title: 'Rastreadores', href: '/rastreadores', rows: [
                    { label: 'Instalados',  v: data?.trackers.installed },
                    { label: 'Em estoque',  v: data?.trackers.stock },
                    { label: 'Manutenção',  v: data?.trackers.maintenance, warn: (data?.trackers.maintenance ?? 0) > 0 },
                  ]},
              ].map((block) => (
                <Link key={block.title} href={block.href} className="block rounded-xl border border-slate-100 bg-slate-50/80 p-4 transition-colors hover:border-brand-200 hover:bg-brand-50/30 dark:border-slate-800 dark:bg-slate-950/50 dark:hover:border-brand-900/50">
                  <p className={`mb-3 ${labelClass}`}>{block.title}</p>
                  <div className="space-y-2">
                    {block.rows.map((r) => (
                      <div key={r.label} className="flex items-center justify-between">
                        <span className="text-[13px] text-slate-500 dark:text-slate-400">{r.label}</span>
                        <strong className={`text-[13px] tabular-nums ${
                          (r as any).danger ? 'text-rose-600 dark:text-rose-400' :
                          (r as any).warn ? 'text-amber-600 dark:text-amber-400' :
                          'text-slate-900 dark:text-white'
                        }`}>{r.v ?? '—'}</strong>
                      </div>
                    ))}
                  </div>
                </Link>
              ))}
            </div>
          </Card>

          {/* Distribuição de ativos */}
          <Card>
            <SectionHeader
              eyebrow="Distribuição"
              title="Ativos por status"
              description="Visualização comparativa entre clientes, veículos e rastreadores por status."
            />
            <div className="mt-4 grid gap-6 md:grid-cols-2">
              <div>
                <p className={`mb-2 ${labelClass}`}>Clientes por status</p>
                <BarChart
                  items={[
                    { label: 'Ativos',   value: data?.clients.active    ?? 0 },
                    { label: 'Inativos', value: data?.clients.inactive  ?? 0 },
                    { label: 'Inadimp.', value: data?.clients.delinquent ?? 0 },
                  ]}
                  height={120}
                  formatValue={(v) => String(v)}
                  emptyMessage="Nenhum cliente cadastrado ainda"
                />
              </div>
              <div>
                <p className={`mb-2 ${labelClass}`}>Ordens de serviço</p>
                <BarChart
                  items={[
                    { label: 'Abertas',    value: data?.service_orders.open        ?? 0 },
                    { label: 'Andamento',  value: data?.service_orders.in_progress ?? 0 },
                    { label: 'Concluídas', value: data?.service_orders.completed   ?? 0 },
                  ]}
                  height={120}
                  primaryColor="#059669"
                  formatValue={(v) => String(v)}
                  emptyMessage="Nenhuma OS registrada ainda"
                />
              </div>
            </div>
          </Card>
        </div>

        {/* ── Right column ────────────────────────────────────────────── */}
        <div className="space-y-6">

          {/* Resumo financeiro */}
          <Card>
            <SectionHeader eyebrow="Financeiro" title="Resumo do mês" />
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center gap-2 text-[13px] text-slate-600 dark:text-slate-400">
                  <TrendingUp className="h-4 w-4" /> Recebido
                </div>
                <strong className="text-[13px] tabular-nums text-slate-900 dark:text-white">{currency(data?.finance.received_month)}</strong>
              </div>
              <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center gap-2 text-[13px] text-slate-600 dark:text-slate-400">
                  <Wallet className="h-4 w-4" /> Pendentes
                </div>
                <strong className="text-[13px] tabular-nums text-slate-900 dark:text-white">{data?.finance.pending_count ?? '—'}</strong>
              </div>
              <div className={`flex items-center justify-between rounded-xl border px-4 py-3 ${
                (data?.finance.overdue_count ?? 0) > 0
                  ? 'border-rose-100 bg-rose-50/80 dark:border-rose-900/40 dark:bg-rose-950/30'
                  : 'border-slate-100 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/50'
              }`}>
                <div className={`flex items-center gap-2 text-[13px] ${
                  (data?.finance.overdue_count ?? 0) > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-600 dark:text-slate-400'
                }`}>
                  <AlertTriangle className="h-4 w-4" /> Vencidas
                </div>
                <strong className={`text-[13px] tabular-nums ${
                  (data?.finance.overdue_count ?? 0) > 0 ? 'text-rose-700 dark:text-rose-400' : 'text-slate-900 dark:text-white'
                }`}>{data?.finance.overdue_count ?? '—'}</strong>
              </div>
            </div>
            {/* Compact CTA */}
            <div className="mt-3 flex justify-end">
              <Link href="/financeiro" className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
                Ver financeiro <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </Card>

          {/* Próximos vencimentos */}
          <Card>
            <SectionHeader eyebrow="Atenção" title="Próximos vencimentos" description="Cobranças nos próximos 7 dias." />
            <div className="mt-4">
              {error ? (
                // Sem 'data' e SEM tentativa em andamento — não deixa o
                // skeleton girando pra sempre quando a carga falhou de vez.
                <div className="flex flex-col items-center gap-2 py-6">
                  <AlertTriangle className="h-8 w-8 text-amber-400 dark:text-amber-500" strokeWidth={1.5} />
                  <p className="text-[13px] font-medium text-slate-500 dark:text-slate-400">Não foi possível carregar — veja o erro acima</p>
                </div>
              ) : !data ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                </div>
              ) : data.upcoming_billings.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-6">
                  <CheckCircle2 className="h-8 w-8 text-emerald-400 dark:text-emerald-500" strokeWidth={1.5} />
                  <p className="text-[13px] font-medium text-slate-500 dark:text-slate-400">Nenhum vencimento nos próximos 7 dias</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {data.upcoming_billings.map((b) => (
                    <div key={b.id} className="flex items-center gap-3 rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950/50">
                      <CalendarClock className={`h-4 w-4 shrink-0 ${b.days_until < 0 ? 'text-rose-500' : b.days_until === 0 ? 'text-amber-500' : 'text-sky-500'}`} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[12px] font-medium text-slate-800 dark:text-slate-200">{b.client_name}</p>
                        <p className="text-[11px] text-slate-400">
                          {fmtDate(b.due_date)} ·{' '}
                          {b.days_until < 0 ? <span className="text-rose-500">{Math.abs(b.days_until)}d atrasado</span>
                            : b.days_until === 0 ? <span className="text-amber-500">hoje</span>
                            : `${b.days_until}d`}
                        </p>
                      </div>
                      <span className="text-[12px] font-semibold tabular-nums text-slate-700 dark:text-slate-300">{currency(b.amount)}</span>
                    </div>
                  ))}
                  <Link href="/financeiro" className="mt-1 flex items-center justify-center gap-1.5 rounded-lg py-2 text-[11px] font-semibold text-brand-600 hover:underline dark:text-brand-400">
                    Ver todos no financeiro <ArrowRight className="h-3 w-3" />
                  </Link>
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </PageShell>
  );
}
