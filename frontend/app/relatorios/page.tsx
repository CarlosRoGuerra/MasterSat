'use client';

import { useEffect, useState, useCallback } from 'react';
import {
  TrendingUp, AlertTriangle, FileText, Download,
  RefreshCw, Users, DollarSign, BarChart2, CheckCircle2,
  CalendarDays, Clock,
} from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, Skeleton } from '@/components/ui/empty-state';
import { ExportButton } from '@/components/ui/export-button';
import { ErrorBanner } from '@/components/ui/error-banner';
import { RevenueChart } from '@/components/ui/revenue-chart';
import { apiFetch, API_URL } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';

/** Abre um export protegido em nova aba (PDF) ou baixa (CSV/Excel). */
async function abrirExport(token: string, path: string, baixarComo?: string) {
  const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    let detalhe = `Erro ${resp.status}`;
    try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
    throw new Error(detalhe);
  }
  const url = URL.createObjectURL(await resp.blob());
  if (baixarComo) {
    const a = document.createElement('a');
    a.href = url; a.download = baixarComo;
    document.body.appendChild(a); a.click(); a.remove();
  } else {
    window.open(url, '_blank');
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

async function abrirPdfInadimplentes(token: string, de?: string, ate?: string) {
  const p = new URLSearchParams({ fmt: 'pdf' });
  if (de) p.set('due_from', de);
  if (ate) p.set('due_to', ate);
  return abrirExport(token, `exports/delinquents?${p}`);
}

/* ── Types ─────────────────────────────────────────────────────────────── */
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

type Preset = 'mes' | 'tri' | 'ano' | 'custom';

/* ── Helpers ───────────────────────────────────────────────────────────── */
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);

const fmtDate = (iso: string) => {
  const [y, m, d] = iso.split('-');
  return `${d}/${m}/${y}`;
};

function friendlyError(msg: string) {
  if (msg === 'Failed to fetch' || msg.includes('NetworkError') || msg.includes('fetch'))
    return 'Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.';
  return msg;
}

function relativeTime(ts: Date | null) {
  if (!ts) return null;
  const diff = Math.floor((Date.now() - ts.getTime()) / 1000);
  if (diff < 60) return 'Atualizado agora';
  if (diff < 3600) return `Atualizado há ${Math.floor(diff / 60)} min`;
  return `Atualizado às ${ts.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

function isoToday() { return new Date().toISOString().slice(0, 10); }
function isoFirstOfYear() { return `${new Date().getFullYear()}-01-01`; }
function isoFirstOfMonth() {
  const d = new Date(); d.setDate(1);
  return d.toISOString().slice(0, 10);
}
function isoMinus(days: number) {
  const d = new Date(); d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/* ── Componente ────────────────────────────────────────────────────────── */
export default function RelatoriosPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(
    ROUTE_ROLES['/relatorios'],
    '/login/admin',
  );

  /* ── Date range state ── */
  const [preset, setPreset] = useState<Preset>('ano');
  const [dateFrom, setDateFrom] = useState(isoFirstOfYear());
  const [dateTo, setDateTo] = useState(isoToday());

  /** Aplica o preset e DEVOLVE o intervalo resultante.
   *
   * Quem clica precisa recarregar com o período novo, mas setDateFrom/setDateTo
   * só valem no próximo render — ler dateFrom/dateTo logo após chamar esta
   * função devolve o período ANTERIOR. Era o que acontecia: cada clique
   * carregava o relatório do preset anterior, e só um segundo clique mostrava
   * o certo. Retornando o intervalo, o chamador usa o valor correto na hora.
   */
  function applyPreset(p: Preset): { from: string; to: string } {
    setPreset(p);
    let from = dateFrom;
    let to = dateTo;
    if (p === 'mes') { from = isoFirstOfMonth(); to = isoToday(); }
    else if (p === 'tri') { from = isoMinus(90); to = isoToday(); }
    else if (p === 'ano') { from = isoFirstOfYear(); to = isoToday(); }
    // 'custom' mantém o que estiver nos inputs
    if (p !== 'custom') { setDateFrom(from); setDateTo(to); }
    return { from, to };
  }

  /* ── Revenue state ── */
  const [revenue, setRevenue] = useState<RevenueMonth[]>([]);
  const [revenueTotals, setRevenueTotals] = useState<RevenueTotals | null>(null);
  const [revenueLoading, setRevenueLoading] = useState(false);

  /* ── Delinquency state ── */
  const [delinquents, setDelinquents] = useState<DelinquentClient[]>([]);
  const [delinquencyStatus, setDelinquencyStatus] = useState<DelinquencyStatus | null>(null);
  const [delinquencyLoading, setDelinquencyLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [showAllDelinquents, setShowAllDelinquents] = useState(false);

  /* ── Relatório de cobranças por período ── */
  const [relSituacao, setRelSituacao] = useState('paga');
  const [relPeriodoPor, setRelPeriodoPor] = useState('pagamento');

  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  /* ── Load functions ── */
  const loadRevenue = useCallback(async (t: string, from: string, to: string) => {
    setRevenueLoading(true);
    try {
      // O período vai inteiro para a API. Antes só o ANO da data inicial era
      // enviado e o recorte acontecia aqui — um intervalo que cruzasse a
      // virada do ano (dez/2025→jan/2026) perdia os meses do segundo ano, que
      // nunca chegavam a ser consultados.
      const params = new URLSearchParams({ date_from: from, date_to: to });
      const data = await apiFetch<{ meses: RevenueMonth[]; totais: RevenueTotals }>(
        `/reports/revenue?${params.toString()}`, {}, t,
      );
      setRevenue(data.meses);
      // Totais vêm calculados do backend, sobre o mesmo conjunto de dados —
      // sem recontagem no cliente, que podia divergir do que a API somou.
      setRevenueTotals(data.totais);
    } catch (e) {
      setError(friendlyError(e instanceof Error ? e.message : 'Erro ao carregar receita'));
    } finally {
      setRevenueLoading(false);
    }
  }, []);

  const loadDelinquents = useCallback(async (t: string) => {
    setDelinquencyLoading(true);
    try {
      const [report, status] = await Promise.all([
        apiFetch<{ clientes: DelinquentClient[] }>('/reports/delinquents', {}, t),
        apiFetch<DelinquencyStatus>('/delinquency/status', {}, t),
      ]);
      setDelinquents(report.clientes);
      setDelinquencyStatus(status);
      setLastRefresh(new Date());
    } catch (e) {
      setError(friendlyError(e instanceof Error ? e.message : 'Erro ao carregar inadimplência'));
    } finally {
      setDelinquencyLoading(false);
    }
  }, []);

  async function runDelinquencyCheck() {
    if (!token) return;
    setRefreshing(true);
    setFeedback('');
    try {
      const result = await apiFetch<{ marcados_inadimplentes: number; restaurados_ativos: number }>(
        '/delinquency/refresh', { method: 'POST' }, token,
      );
      setFeedback(
        `Verificação concluída — ${result.marcados_inadimplentes} marcados inadimplentes, ${result.restaurados_ativos} restaurados.`,
      );
      await loadDelinquents(token);
    } catch (e) {
      setError(friendlyError(e instanceof Error ? e.message : 'Erro na verificação'));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadRevenue(token, dateFrom, dateTo);
    loadDelinquents(token);
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Derived ── */
  const allClear = delinquencyStatus
    ? delinquencyStatus.clientes_inadimplentes === 0
      && delinquencyStatus.cobrancas_vencidas === 0
      && delinquencyStatus.valor_total_vencido === 0
    : false;

  const visibleDelinquents = showAllDelinquents ? delinquents : delinquents.slice(0, 3);

  const presets: { key: Preset; label: string }[] = [
    { key: 'mes', label: 'Este mês' },
    { key: 'tri', label: 'Últimos 3 meses' },
    { key: 'ano', label: 'Este ano' },
    { key: 'custom', label: 'Personalizado' },
  ];

  /* ── Typography helpers ── */
  const labelClass = 'text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 dark:text-slate-500';
  const kpiValueClass = 'mt-1.5 text-[28px] font-medium leading-none tabular-nums';

  return (
    <PageShell title="Relatórios" description="Receita por período, inadimplência e exportações.">

      {/* ── Errors / feedback ─────────────────────────────────────────────── */}
      {(guardError || error) && (
        <div className="mb-4">
          <ErrorBanner message={guardError || error} />
          {error && !guardError && token && (
            <button
              onClick={() => { setError(''); loadRevenue(token, dateFrom, dateTo); loadDelinquents(token); }}
              className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            >
              Tentar novamente
            </button>
          )}
        </div>
      )}
      {feedback && (
        <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">
          {feedback}
        </div>
      )}

      {/* ── KPI cards OR all-clear banner ────────────────────────────────── */}
      {delinquencyStatus && (
        allClear ? (
          <div className="mb-6 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4 dark:border-emerald-900/40 dark:bg-emerald-950/20">
            <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
            <div>
              <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">
                Nenhuma pendência — todos os clientes estão em dia
              </p>
              <p className="mt-0.5 text-xs text-emerald-600 dark:text-emerald-500">
                Sem cobranças vencidas ou clientes inadimplentes no momento
              </p>
            </div>
          </div>
        ) : (
          <section className="mb-6 grid gap-4 sm:grid-cols-3">
            <Card>
              <div className="flex items-center justify-between">
                <div>
                  <p className={labelClass}>Clientes inadimplentes</p>
                  <p className={`${kpiValueClass} ${delinquencyStatus.clientes_inadimplentes > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-800 dark:text-white'}`}>
                    {delinquencyStatus.clientes_inadimplentes}
                  </p>
                </div>
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-50 dark:bg-rose-950/30">
                  <Users className="h-5 w-5 text-rose-500" />
                </div>
              </div>
            </Card>
            <Card>
              <div className="flex items-center justify-between">
                <div>
                  <p className={labelClass}>Cobranças vencidas</p>
                  <p className={`${kpiValueClass} ${delinquencyStatus.cobrancas_vencidas > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-800 dark:text-white'}`}>
                    {delinquencyStatus.cobrancas_vencidas}
                  </p>
                </div>
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-50 dark:bg-amber-950/30">
                  <AlertTriangle className="h-5 w-5 text-amber-500" />
                </div>
              </div>
            </Card>
            <Card>
              <div className="flex items-center justify-between">
                <div>
                  <p className={labelClass}>Valor vencido</p>
                  <p className={`${kpiValueClass} text-xl ${delinquencyStatus.valor_total_vencido > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-800 dark:text-white'}`}>
                    {fmt(delinquencyStatus.valor_total_vencido)}
                  </p>
                  {delinquencyStatus.cobrancas_vencidas > 0 && (
                    <p className="mt-1 text-[11px] text-rose-500 dark:text-rose-400">
                      {delinquencyStatus.cobrancas_vencidas} cobranças vencidas
                    </p>
                  )}
                </div>
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-50 dark:bg-rose-950/30">
                  <DollarSign className="h-5 w-5 text-rose-500" />
                </div>
              </div>
            </Card>
          </section>
        )
      )}

      {/* ── Date range filter ────────────────────────────────────────────── */}
      <Card className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <CalendarDays className="h-4 w-4 text-brand-600 shrink-0" />
          <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Período</p>

          {/* Preset buttons */}
          <div className="flex flex-wrap gap-1.5">
            {presets.map(p => (
              <button
                key={p.key}
                type="button"
                onClick={() => {
                  const { from, to } = applyPreset(p.key);
                  if (token) loadRevenue(token, from, to);
                }}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                  preset === p.key
                    ? 'border-brand-500 bg-brand-500 text-black'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-brand-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Date inputs (always visible for precision) */}
          <div className="flex items-center gap-2 ml-auto">
            <label className="flex items-center gap-1.5">
              <span className="text-[11px] text-slate-400">De</span>
              <input
                type="date"
                value={dateFrom}
                onChange={e => { setDateFrom(e.target.value); setPreset('custom'); }}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              />
            </label>
            <label className="flex items-center gap-1.5">
              <span className="text-[11px] text-slate-400">Até</span>
              <input
                type="date"
                value={dateTo}
                onChange={e => { setDateTo(e.target.value); setPreset('custom'); }}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              />
            </label>
            <Button
              onClick={() => { if (token) loadRevenue(token, dateFrom, dateTo); }}
              className="text-xs"
            >
              Aplicar
            </Button>
          </div>
        </div>

        {/* Active period badge */}
        <div className="mt-3 flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
            <CalendarDays className="h-3 w-3" />
            Exibindo: {fmtDate(dateFrom)} – {fmtDate(dateTo)}
          </span>
        </div>
      </Card>

      {/* ── Relatório de cobranças por período ───────────────────────────── */}
      <Card className="mb-6">
        <div className="mb-1 flex items-center gap-2">
          <FileText className="h-4 w-4 text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Relatório de cobranças</h3>
        </div>
        <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
          Ex.: quais clientes <strong>pagaram</strong> entre dois dias — escolha “Pagas” e filtre por data de pagamento.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Situação</span>
            <select
              value={relSituacao}
              onChange={(e) => setRelSituacao(e.target.value)}
              className="w-44 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="paga">Pagas (baixadas)</option>
              <option value="pendente">Em aberto</option>
              <option value="vencida">Vencidas</option>
              <option value="todas">Todas</option>
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Filtrar período por</span>
            <select
              value={relPeriodoPor}
              onChange={(e) => setRelPeriodoPor(e.target.value)}
              className="w-48 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="pagamento">Data de pagamento</option>
              <option value="vencimento">Data de vencimento</option>
            </select>
          </label>
          <p className="pb-2 text-xs text-slate-400">
            Período: {fmtDate(dateFrom)} – {fmtDate(dateTo)}
          </p>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => token && abrirExport(
                token,
                `exports/billings-report?fmt=csv&situacao=${relSituacao}&periodo_por=${relPeriodoPor}&date_from=${dateFrom}&date_to=${dateTo}`,
                'relatorio-cobrancas.csv',
              ).catch(e => setError(e instanceof Error ? e.message : 'Erro ao gerar o relatório'))}
            >
              <Download className="h-4 w-4" /> CSV
            </Button>
            <Button
              onClick={() => token && abrirExport(
                token,
                `exports/billings-report?fmt=pdf&situacao=${relSituacao}&periodo_por=${relPeriodoPor}&date_from=${dateFrom}&date_to=${dateTo}`,
              ).catch(e => setError(e instanceof Error ? e.message : 'Erro ao gerar o relatório'))}
            >
              <FileText className="h-4 w-4" /> Gerar PDF
            </Button>
          </div>
        </div>
      </Card>

      {/* ── Exportar dados (no topo, antes dos gráficos) ─────────────────── */}
      <Card className="mb-6">
        <div className="mb-4 flex items-center gap-2">
          <Download className="h-4 w-4 text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Exportar dados</h3>
          <span className="ml-1 text-[11px] text-slate-400 dark:text-slate-500">
            período: {fmtDate(dateFrom)} – {fmtDate(dateTo)}
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: 'Clientes',      path: 'exports/clients',    basename: 'clientes',     icon: Users,          color: 'bg-blue-50 text-blue-600 dark:bg-blue-950/30 dark:text-blue-400' },
            { label: 'Veículos',      path: 'exports/vehicles',   basename: 'veiculos',     icon: FileText,       color: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-400' },
            { label: 'Rastreadores',  path: 'exports/trackers',   basename: 'rastreadores', icon: TrendingUp,     color: 'bg-brand-50 text-brand-600 dark:bg-brand-950/30 dark:text-brand-400' },
            { label: 'Inadimplentes', path: 'exports/delinquents',basename: 'inadimplentes',icon: AlertTriangle,  color: 'bg-rose-50 text-rose-600 dark:bg-rose-950/30 dark:text-rose-400' },
          ].map(({ label, path, basename, icon: Icon, color }) =>
            token ? (
              <div key={label} className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-700">
                <div className="flex items-center gap-3">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${color}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</p>
                    <p className="text-[11px] text-slate-400">{label === 'Inadimplentes' ? 'CSV · Excel · PDF' : 'CSV · Excel'}</p>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {label === 'Inadimplentes' && (
                    <button
                      type="button"
                      title="Imprimir em PDF"
                      onClick={() => abrirPdfInadimplentes(token, dateFrom, dateTo).catch(e => setError(e instanceof Error ? e.message : 'Erro ao gerar PDF'))}
                      className="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-[11px] font-semibold text-rose-700 transition hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400"
                    >
                      PDF
                    </button>
                  )}
                  <ExportButton path={path} basename={basename} token={token} />
                </div>
              </div>
            ) : null,
          )}
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">

        {/* ── Receita por período ─────────────────────────────────────────── */}
        <Card>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <BarChart2 className="h-4 w-4 text-brand-600" />
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Receita por período</h3>
            </div>
            {token && (
              <ExportButton
                path={`exports/billings?date_from=${dateFrom}&date_to=${dateTo}`}
                basename={`cobrancas_${dateFrom.slice(0, 7)}`}
                token={token}
              />
            )}
          </div>

          {/* KPI mini-row */}
          {revenueTotals && (
            <div className="mb-5 grid grid-cols-3 gap-2">
              {[
                { label: 'Emitido',    value: revenueTotals.total_emitido,  color: 'text-slate-700 dark:text-slate-200' },
                { label: 'Recebido',   value: revenueTotals.total_recebido, color: 'text-emerald-700 dark:text-emerald-400' },
                { label: 'Em aberto',  value: revenueTotals.total_aberto,   color: revenueTotals.total_aberto > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500' },
              ].map(({ label, value, color }) => (
                <div key={label} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900/50">
                  <p className={labelClass}>{label}</p>
                  <p className={`mt-1 text-sm font-bold tabular-nums ${color}`}>{fmt(value)}</p>
                </div>
              ))}
            </div>
          )}

          {/* Chart */}
          {revenueLoading ? (
            <div className="space-y-2 pt-2">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : (
            <RevenueChart data={revenue} />
          )}

          {/* Table (collapsible detail) */}
          {!revenueLoading && revenue.length > 0 && (
            <details className="mt-4 group">
              <summary className="cursor-pointer select-none text-[11px] font-semibold uppercase tracking-widest text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 list-none flex items-center gap-1">
                <span className="group-open:hidden">▶</span>
                <span className="hidden group-open:inline">▼</span>
                Ver tabela detalhada
              </summary>
              <div className="mt-3 overflow-x-auto">
                <Table>
                  <TableHead>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Mês</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Cobranças</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Emitido</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Recebido</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Em aberto</Th>
                  </TableHead>
                  <TableBody>
                    {revenue.map((m) => (
                      <Tr key={m.label}>
                        <Td className="text-[13px] font-medium">{m.label}</Td>
                        <Td className="text-center text-[13px]">{m.total_cobrancas}</Td>
                        <Td className="font-mono text-[13px]">{fmt(m.total_emitido)}</Td>
                        <Td className="font-mono text-[13px] text-emerald-700 dark:text-emerald-400">{fmt(m.total_recebido)}</Td>
                        <Td className={`font-mono text-[13px] ${m.total_aberto > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400'}`}>
                          {fmt(m.total_aberto)}
                        </Td>
                      </Tr>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </details>
          )}
        </Card>

        {/* ── Inadimplência ───────────────────────────────────────────────── */}
        <Card>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-rose-500" />
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Inadimplência</h3>
            </div>
            <div className="flex items-center gap-2">
              {/* Timestamp */}
              {lastRefresh && (
                <span className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500">
                  <Clock className="h-3 w-3" />
                  {relativeTime(lastRefresh)}
                </span>
              )}
              <Button
                variant="secondary"
                onClick={runDelinquencyCheck}
                disabled={refreshing || guardLoading}
                className="gap-1.5 text-xs"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
                {refreshing ? 'Atualizando…' : 'Atualizar agora'}
              </Button>
              {token && (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => abrirPdfInadimplentes(token, dateFrom, dateTo).catch(e => setError(e instanceof Error ? e.message : 'Erro ao gerar PDF'))}
                    className="gap-1.5 text-xs"
                  >
                    <FileText className="h-3.5 w-3.5" /> Imprimir PDF
                  </Button>
                  <ExportButton
                    path="exports/delinquents"
                    basename="inadimplentes"
                    token={token}
                  />
                </>
              )}
            </div>
          </div>

          <div>
            {delinquencyLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
              </div>
            ) : error ? (
              // "Tudo certo" verde não pode aparecer quando a carga FALHOU — o
              // operador leria como "sem inadimplentes" quando, na verdade, o
              // relatório nem chegou a carregar.
              <div className="flex flex-col items-center gap-2 py-10">
                <AlertTriangle className="h-10 w-10 text-amber-400 dark:text-amber-500" strokeWidth={1.5} />
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Não foi possível carregar a inadimplência</p>
                <p className="text-xs text-slate-400">Veja o erro acima e tente novamente.</p>
              </div>
            ) : delinquents.length === 0 ? (
              /* ── All-clear empty state ── */
              <div className="flex flex-col items-center gap-2 py-10">
                <CheckCircle2 className="h-10 w-10 text-emerald-400 dark:text-emerald-500" strokeWidth={1.5} />
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Nenhuma inadimplência</p>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Todos os clientes estão com pagamentos em dia
                </p>
                {lastRefresh && (
                  <p className="mt-1 text-[11px] text-slate-300 dark:text-slate-600">
                    Última verificação: {lastRefresh.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
                  </p>
                )}
              </div>
            ) : (
              <>
                <Table>
                  <TableHead>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Cliente</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Cobranças</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Valor vencido</Th>
                    <Th className="text-[11px] uppercase tracking-[0.04em]">Atraso</Th>
                  </TableHead>
                  <TableBody>
                    {visibleDelinquents.map((d) => (
                      <Tr key={d.client_id}>
                        <Td>
                          <p className="text-[13px] font-medium text-slate-900 dark:text-white">{d.nome}</p>
                          <p className="text-[11px] text-slate-400">{d.cpf_cnpj}</p>
                        </Td>
                        <Td className="text-center text-[13px]">{d.qtd_cobrancas_vencidas}</Td>
                        <Td className="font-mono text-[13px] font-semibold text-rose-600 dark:text-rose-400">
                          {fmt(d.valor_total_vencido)}
                        </Td>
                        <Td className="text-center">
                          <span className={`text-[13px] font-semibold ${d.dias_atraso_max > 30 ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400'}`}>
                            {d.dias_atraso_max}d
                          </span>
                        </Td>
                      </Tr>
                    ))}
                  </TableBody>
                </Table>
                {delinquents.length > 3 && (
                  <button
                    type="button"
                    onClick={() => setShowAllDelinquents(p => !p)}
                    className="mt-3 w-full rounded-lg border border-slate-200 py-2 text-xs font-semibold text-slate-500 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                  >
                    {showAllDelinquents
                      ? 'Ver menos'
                      : `Ver todos (${delinquents.length})`}
                  </button>
                )}
              </>
            )}
          </div>
        </Card>
      </div>
    </PageShell>
  );
}
