'use client';

import { useEffect, useState } from 'react';
import {
  TrendingUp, AlertTriangle, FileText, Download,
  RefreshCw, Users, DollarSign, BarChart2,
} from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, Skeleton } from '@/components/ui/empty-state';
import { ExportButton } from '@/components/ui/export-button';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { downloadExport, exportFilename } from '@/lib/export';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RevenueMonth = {
  ano: number; mes: number; label: string;
  total_cobrancas: number; total_emitido: number;
  total_recebido: number; total_aberto: number;
};

type RevenueTotals = {
  total_emitido: number; total_recebido: number;
  total_aberto: number; taxa_recebimento: number;
};

type DelinquentClient = {
  client_id: number; nome: string; cpf_cnpj: string;
  email?: string; phone?: string; status_atual: string;
  qtd_cobrancas_vencidas: number; valor_total_vencido: number;
  vencimento_mais_antigo?: string; dias_atraso_max: number;
};

type DelinquencyStatus = {
  clientes_inadimplentes: number;
  cobrancas_vencidas: number;
  valor_total_vencido: number;
};

function fmt(v: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
}

// ---------------------------------------------------------------------------
// Componente
// ---------------------------------------------------------------------------

export default function RelatoriosPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(
    ['admin', 'financeiro'],
    '/login/admin',
  );

  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(String(thisYear));
  const [revenue, setRevenue] = useState<RevenueMonth[]>([]);
  const [revenueTotals, setRevenueTotals] = useState<RevenueTotals | null>(null);
  const [revenueLoading, setRevenueLoading] = useState(false);

  const [delinquents, setDelinquents] = useState<DelinquentClient[]>([]);
  const [delinquencyStatus, setDelinquencyStatus] = useState<DelinquencyStatus | null>(null);
  const [delinquencyLoading, setDelinquencyLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  // Carrega receita
  async function loadRevenue(t: string, y: string) {
    setRevenueLoading(true);
    try {
      const data = await apiFetch<{ meses: RevenueMonth[]; totais: RevenueTotals }>(
        `/reports/revenue?year=${y}`, {}, t,
      );
      setRevenue(data.meses);
      setRevenueTotals(data.totais);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar receita');
    } finally {
      setRevenueLoading(false);
    }
  }

  // Carrega inadimplentes
  async function loadDelinquents(t: string) {
    setDelinquencyLoading(true);
    try {
      const [report, status] = await Promise.all([
        apiFetch<{ clientes: DelinquentClient[] }>('/reports/delinquents', {}, t),
        apiFetch<DelinquencyStatus>('/delinquency/status', {}, t),
      ]);
      setDelinquents(report.clientes);
      setDelinquencyStatus(status);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar inadimplência');
    } finally {
      setDelinquencyLoading(false);
    }
  }

  // Executa verificação de inadimplência
  async function runDelinquencyCheck() {
    if (!token) return;
    setRefreshing(true);
    setFeedback('');
    try {
      const result = await apiFetch<{ marcados_inadimplentes: number; restaurados_ativos: number }>(
        '/delinquency/refresh', { method: 'POST' }, token,
      );
      setFeedback(
        `Verificação concluída — ${result.marcados_inadimplentes} cliente(s) marcados como inadimplentes, ${result.restaurados_ativos} restaurados como ativos.`,
      );
      await loadDelinquents(token);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro na verificação');
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadRevenue(token, year);
    loadDelinquents(token);
  }, [token]);

  const years = Array.from({ length: 5 }, (_, i) => String(thisYear - i));

  return (
    <PageShell title="Relatórios" description="Receita por período, inadimplência e exportações.">
      {(guardError || error) && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
          {guardError || error}
        </div>
      )}
      {feedback && (
        <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">
          {feedback}
        </div>
      )}

      {/* ── KPIs de inadimplência ─────────────────────────────────────────── */}
      {delinquencyStatus && (
        <section className="grid gap-4 sm:grid-cols-3">
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">Clientes inadimplentes</p>
                <p className="mt-2 text-3xl font-bold text-rose-600 dark:text-rose-400">
                  {delinquencyStatus.clientes_inadimplentes}
                </p>
              </div>
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-50 dark:bg-rose-950/30">
                <Users className="h-5 w-5 text-rose-600 dark:text-rose-400" />
              </div>
            </div>
          </Card>
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">Cobranças vencidas</p>
                <p className="mt-2 text-3xl font-bold text-amber-600 dark:text-amber-400">
                  {delinquencyStatus.cobrancas_vencidas}
                </p>
              </div>
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-50 dark:bg-amber-950/30">
                <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400" />
              </div>
            </div>
          </Card>
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">Valor em aberto</p>
                <p className="mt-2 text-2xl font-bold text-rose-600 dark:text-rose-400">
                  {fmt(delinquencyStatus.valor_total_vencido)}
                </p>
              </div>
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-50 dark:bg-rose-950/30">
                <DollarSign className="h-5 w-5 text-rose-600 dark:text-rose-400" />
              </div>
            </div>
          </Card>
        </section>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        {/* ── Receita por período ─────────────────────────────────────────── */}
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <BarChart2 className="h-4 w-4 text-brand-600" />
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Receita por mês</h3>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={year}
                onChange={(e) => { setYear(e.target.value); if (token) loadRevenue(token, e.target.value); }}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              >
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              {token && (
                <ExportButton
                  path={`exports/billings?date_from=${year}-01-01&date_to=${year}-12-31`}
                  basename={`cobrancas_${year}`}
                  token={token}
                />
              )}
            </div>
          </div>

          {revenueTotals && (
            <div className="mt-4 grid grid-cols-3 gap-2">
              {[
                { label: 'Emitido', value: revenueTotals.total_emitido, color: 'text-slate-700 dark:text-slate-300' },
                { label: 'Recebido', value: revenueTotals.total_recebido, color: 'text-emerald-700 dark:text-emerald-400' },
                { label: 'Em aberto', value: revenueTotals.total_aberto, color: 'text-rose-600 dark:text-rose-400' },
              ].map(({ label, value, color }) => (
                <div key={label} className="rounded-xl border border-slate-100 bg-slate-50 p-3 text-center dark:border-slate-800 dark:bg-slate-900/50">
                  <p className="text-[11px] text-slate-400">{label}</p>
                  <p className={`mt-1 text-sm font-bold tabular-nums ${color}`}>{fmt(value)}</p>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4">
            {revenueLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
              </div>
            ) : revenue.length === 0 ? (
              <EmptyState icon={BarChart2} title="Sem dados para o período" description="Selecione outro ano ou aguarde cobranças serem geradas." />
            ) : (
              <Table>
                <TableHead>
                  <Th>Mês</Th>
                  <Th>Cobranças</Th>
                  <Th>Emitido</Th>
                  <Th>Recebido</Th>
                  <Th>Em aberto</Th>
                </TableHead>
                <TableBody>
                  {revenue.map((m) => (
                    <Tr key={m.label}>
                      <Td className="font-medium">{m.label}</Td>
                      <Td className="text-center text-sm">{m.total_cobrancas}</Td>
                      <Td className="font-mono text-sm">{fmt(m.total_emitido)}</Td>
                      <Td className="font-mono text-sm text-emerald-700 dark:text-emerald-400">{fmt(m.total_recebido)}</Td>
                      <Td className="font-mono text-sm text-rose-600 dark:text-rose-400">{fmt(m.total_aberto)}</Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </Card>

        {/* ── Inadimplência ───────────────────────────────────────────────── */}
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-rose-500" />
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Relatório de inadimplência</h3>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={runDelinquencyCheck}
                disabled={refreshing || guardLoading}
                className="gap-1.5 text-xs"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
                Atualizar agora
              </Button>
              {token && (
                <ExportButton
                  path="exports/delinquents"
                  basename="inadimplentes"
                  token={token}
                />
              )}
            </div>
          </div>

          <div className="mt-4">
            {delinquencyLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
              </div>
            ) : delinquents.length === 0 ? (
              <EmptyState icon={AlertTriangle} title="Nenhum cliente inadimplente" description="Todos os clientes estão com situação regularizada." />
            ) : (
              <Table>
                <TableHead>
                  <Th>Cliente</Th>
                  <Th>Cobranças</Th>
                  <Th>Valor vencido</Th>
                  <Th>Atraso (dias)</Th>
                  <Th>Status</Th>
                </TableHead>
                <TableBody>
                  {delinquents.map((d) => (
                    <Tr key={d.client_id}>
                      <Td>
                        <p className="font-medium text-slate-900 dark:text-white">{d.nome}</p>
                        <p className="text-xs text-slate-400">{d.cpf_cnpj}</p>
                      </Td>
                      <Td className="text-center text-sm">{d.qtd_cobrancas_vencidas}</Td>
                      <Td className="font-mono font-semibold text-rose-600 dark:text-rose-400">
                        {fmt(d.valor_total_vencido)}
                      </Td>
                      <Td className="text-center">
                        <span className={`text-sm font-semibold ${d.dias_atraso_max > 30 ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400'}`}>
                          {d.dias_atraso_max}d
                        </span>
                      </Td>
                      <Td>
                        <Badge variant={statusVariant(d.status_atual)}>{statusLabel(d.status_atual)}</Badge>
                      </Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </Card>
      </div>

      {/* ── Exportações rápidas ──────────────────────────────────────────── */}
      <Card className="mt-6">
        <div className="flex items-center gap-2 mb-4">
          <Download className="h-4 w-4 text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Exportações rápidas</h3>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: 'Clientes', path: 'exports/clients', basename: 'clientes', icon: Users, color: 'bg-blue-50 text-blue-600 dark:bg-blue-950/30 dark:text-blue-400' },
            { label: 'Veículos', path: 'exports/vehicles', basename: 'veiculos', icon: FileText, color: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-400' },
            { label: 'Rastreadores', path: 'exports/trackers', basename: 'rastreadores', icon: TrendingUp, color: 'bg-brand-50 text-brand-600 dark:bg-brand-950/30 dark:text-brand-400' },
            { label: 'Inadimplentes', path: 'exports/delinquents', basename: 'inadimplentes', icon: AlertTriangle, color: 'bg-rose-50 text-rose-600 dark:bg-rose-950/30 dark:text-rose-400' },
          ].map(({ label, path, basename, icon: Icon, color }) => (
            token ? (
              <div key={label} className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-700">
                <div className="flex items-center gap-3">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${color}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <span className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</span>
                </div>
                <ExportButton path={path} basename={basename} token={token} />
              </div>
            ) : null
          ))}
        </div>
      </Card>
    </PageShell>
  );
}
