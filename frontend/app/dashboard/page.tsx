'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  Users,
  Car,
  Radio,
  ClipboardList,
  Wallet,
  AlertTriangle,
  TrendingUp,
  ArrowRight,
} from 'lucide-react';
import { BarChart } from '@/components/ui/bar-chart';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/empty-state';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type DashboardData = {
  clients: { active: number; inactive: number; delinquent: number };
  vehicles: { total: number };
  trackers: { installed: number; stock: number; maintenance: number };
  service_orders: { open: number; in_progress: number; completed: number };
  finance: { pending_count: number; overdue_count: number; received_month: number };
};

const modules = [
  { href: '/clientes',       label: 'Clientes',             description: 'Cadastro, documentação e histórico.',      icon: Users },
  { href: '/veiculos',       label: 'Veículos',             description: 'Ativos, vínculos e desinstalação.',         icon: Car },
  { href: '/rastreadores',   label: 'Rastreadores',         description: 'Equipamentos, IMEIs e técnico.',            icon: Radio },
  { href: '/ordens-servico', label: 'Ordens de serviço',    description: 'Abertura, execução e evidências.',          icon: ClipboardList },
  { href: '/financeiro',     label: 'Financeiro',           description: 'Planos, contratos e faturamento.',          icon: Wallet },
];

function currency(value?: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0);
}

function ProgressBar({ value, tone = 'brand' }: { value: number; tone?: 'brand' | 'success' | 'warning' | 'danger' }) {
  const barClass = {
    brand:   'bg-brand-600',
    success: 'bg-emerald-500',
    warning: 'bg-amber-500',
    danger:  'bg-rose-500',
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
      <div className={`h-1.5 rounded-full transition-all ${barClass}`} style={{ width: `${Math.min(Math.max(value, 0), 100)}%` }} />
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState('');
  const { token, loading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');

  useEffect(() => {
    if (!token) return;
    apiFetch<DashboardData>('/dashboard', {}, token)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Não foi possível carregar o dashboard.'));
  }, [token]);

  const totals = useMemo(() => {
    const active    = data?.clients.active    ?? 0;
    const inactive  = data?.clients.inactive  ?? 0;
    const delinquent = data?.clients.delinquent ?? 0;
    const clientTotal = active + inactive + delinquent;
    const osOpen      = data?.service_orders.open        ?? 0;
    const osProgress  = data?.service_orders.in_progress ?? 0;
    const osDone      = data?.service_orders.completed   ?? 0;
    const osTotal     = osOpen + osProgress + osDone;
    const finTotal    = (data?.finance.pending_count ?? 0) + (data?.finance.overdue_count ?? 0);
    return {
      clientTotal,
      clientHealthy: clientTotal ? Math.round((active / clientTotal) * 100) : 0,
      osCompletion:  osTotal ? Math.round((osDone / osTotal) * 100) : 0,
      financeRisk:   finTotal ? Math.round(((data?.finance.overdue_count ?? 0) / finTotal) * 100) : 0,
    };
  }, [data]);

  return (
    <PageShell
      title="Dashboard"
      description="Leitura executiva da operação — cadastros, campo e financeiro."
    >
      {(guardError || error) && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
          {guardError || error}
        </div>
      )}

      {/* KPI row */}
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
            <StatCard label="Clientes ativos"        value={data?.clients.active ?? '—'}             hint="Base ativa em acompanhamento"   tone="brand"   icon={<Users className="h-5 w-5" />} />
            <StatCard label="Rastreadores instalados" value={data?.trackers.installed ?? '—'}         hint="Equipamentos em produção"        tone="success" icon={<Radio className="h-5 w-5" />} />
            <StatCard label="OS em andamento"         value={data?.service_orders.in_progress ?? '—'} hint="Ordens abertas no campo"         tone="warning" icon={<ClipboardList className="h-5 w-5" />} />
            <StatCard label="Receita do mês"          value={currency(data?.finance.received_month)}  hint="Recebimentos consolidados"       tone="default" icon={<Wallet className="h-5 w-5" />} />
          </>
        )}
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_360px]">
        {/* Left column */}
        <div className="space-y-6">
          {/* Saúde da operação */}
          <Card>
            <SectionHeader
              eyebrow="Indicadores"
              title="Saúde da operação"
              description="Proporção de clientes ativos, conclusão de OS e risco de inadimplência."
            />
            <div className="mt-5 grid gap-4 md:grid-cols-3">
              {[
                { label: 'Clientes ativos', pct: totals.clientHealthy, tone: 'success' as const, detail: `${data?.clients.active ?? 0} de ${totals.clientTotal}` },
                { label: 'Conclusão de OS', pct: totals.osCompletion,  tone: 'brand'   as const, detail: `${data?.service_orders.completed ?? 0} concluídas` },
                { label: 'Risco financeiro', pct: totals.financeRisk,  tone: 'danger'  as const, detail: `${data?.finance.overdue_count ?? 0} vencidas` },
              ].map((item) => (
                <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">{item.label}</p>
                    <span className="text-lg font-bold text-slate-900 dark:text-white">{item.pct}%</span>
                  </div>
                  <div className="mt-3"><ProgressBar value={item.pct} tone={item.tone} /></div>
                  <p className="mt-2 text-xs text-slate-400">{item.detail}</p>
                </div>
              ))}
            </div>
          </Card>

          {/* Módulos */}
          <Card>
            <SectionHeader eyebrow="Módulos" title="Indicadores por módulo" />
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { title: 'Clientes', rows: [
                    { label: 'Ativos',        v: data?.clients.active    },
                    { label: 'Inativos',      v: data?.clients.inactive  },
                    { label: 'Inadimplentes', v: data?.clients.delinquent },
                  ]},
                { title: 'Veículos', rows: [
                    { label: 'Total',           v: data?.vehicles.total },
                    { label: 'Com rastreador',  v: data?.trackers.installed },
                    { label: 'Sem rastreador',  v: Math.max((data?.vehicles.total ?? 0) - (data?.trackers.installed ?? 0), 0) },
                  ]},
                { title: 'Ordens de serviço', rows: [
                    { label: 'Abertas',      v: data?.service_orders.open        },
                    { label: 'Em andamento', v: data?.service_orders.in_progress },
                    { label: 'Concluídas',   v: data?.service_orders.completed   },
                  ]},
                { title: 'Rastreadores', rows: [
                    { label: 'Instalados',    v: data?.trackers.installed   },
                    { label: 'Em estoque',    v: data?.trackers.stock       },
                    { label: 'Manutenção',    v: data?.trackers.maintenance },
                  ]},
              ].map((block) => (
                <div key={block.title} className="rounded-xl border border-slate-100 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
                  <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-500">{block.title}</p>
                  <div className="space-y-2">
                    {block.rows.map((r) => (
                      <div key={r.label} className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">{r.label}</span>
                        <strong className="tabular-nums text-slate-900 dark:text-white">{r.v ?? '—'}</strong>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* Gráfico de distribuição */}
          <Card>
            <SectionHeader eyebrow="Gráfico" title="Distribuição de ativos" description="Visualização comparativa entre clientes, veículos e rastreadores por status." />
            <div className="mt-4 grid gap-6 md:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-slate-500">Clientes por status</p>
                <BarChart
                  items={[
                    { label: 'Ativos',    value: data?.clients.active    ?? 0 },
                    { label: 'Inativos',  value: data?.clients.inactive  ?? 0 },
                    { label: 'Inadimp.', value: data?.clients.delinquent ?? 0 },
                  ]}
                  height={120}
                  formatValue={(v) => String(v)}
                />
              </div>
              <div>
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-slate-500">Ordens de serviço</p>
                <BarChart
                  items={[
                    { label: 'Abertas',   value: data?.service_orders.open        ?? 0 },
                    { label: 'Andamento', value: data?.service_orders.in_progress ?? 0 },
                    { label: 'Concluídas',value: data?.service_orders.completed   ?? 0 },
                  ]}
                  height={120}
                  primaryColor="#059669"
                  formatValue={(v) => String(v)}
                />
              </div>
            </div>
          </Card>
        </div>

        {/* Right column */}
        <div className="space-y-6">
          {/* Alertas */}
          {(data && (data.finance.overdue_count > 0 || data.service_orders.open > 0)) && (
            <Card>
              <SectionHeader eyebrow="Alertas" title="Itens que precisam de atenção" />
              <div className="mt-4 space-y-2">
                {data.finance.overdue_count > 0 && (
                  <Link href="/financeiro" className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm transition hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:hover:bg-amber-950/50">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                    <span className="text-amber-800 dark:text-amber-300">
                      <strong>{data.finance.overdue_count}</strong> cobrança(s) vencida(s)
                    </span>
                    <ArrowRight className="ml-auto h-4 w-4 text-amber-400" />
                  </Link>
                )}
                {data.service_orders.open > 0 && (
                  <Link href="/ordens-servico" className="flex items-center gap-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm transition hover:bg-blue-100 dark:border-blue-900/40 dark:bg-blue-950/30 dark:hover:bg-blue-950/50">
                    <ClipboardList className="h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" />
                    <span className="text-blue-800 dark:text-blue-300">
                      <strong>{data.service_orders.open}</strong> OS aberta(s) sem andamento
                    </span>
                    <ArrowRight className="ml-auto h-4 w-4 text-blue-400" />
                  </Link>
                )}
              </div>
            </Card>
          )}

          {/* Receita */}
          <Card>
            <SectionHeader eyebrow="Financeiro" title="Resumo do mês" />
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
                  <TrendingUp className="h-4 w-4" />
                  Recebido
                </div>
                <strong className="text-sm tabular-nums text-slate-900 dark:text-white">{currency(data?.finance.received_month)}</strong>
              </div>
              <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/50">
                <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
                  <Wallet className="h-4 w-4" />
                  Pendentes
                </div>
                <strong className="text-sm tabular-nums text-slate-900 dark:text-white">{data?.finance.pending_count ?? '—'}</strong>
              </div>
              <div className="flex items-center justify-between rounded-xl border border-rose-100 bg-rose-50/80 px-4 py-3 dark:border-rose-900/40 dark:bg-rose-950/30">
                <div className="flex items-center gap-2 text-sm text-rose-600 dark:text-rose-400">
                  <AlertTriangle className="h-4 w-4" />
                  Vencidas
                </div>
                <strong className="text-sm tabular-nums text-rose-700 dark:text-rose-400">{data?.finance.overdue_count ?? '—'}</strong>
              </div>
            </div>
            <div className="mt-4">
              <Link href="/financeiro"><Button className="w-full">Ver financeiro</Button></Link>
            </div>
          </Card>

          {/* Acesso rápido */}
          <Card>
            <SectionHeader eyebrow="Atalhos" title="Acesso rápido" />
            <div className="mt-4 space-y-1">
              {modules.map((item) => {
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
                  >
                    <Icon className="h-4 w-4 shrink-0 text-slate-400" />
                    <div className="min-w-0 flex-1">
                      <span className="font-medium text-slate-700 dark:text-slate-300">{item.label}</span>
                    </div>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-300 dark:text-slate-600" />
                  </Link>
                );
              })}
            </div>
          </Card>
        </div>
      </div>
    </PageShell>
  );
}
