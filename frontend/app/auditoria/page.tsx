'use client';

import { useEffect, useMemo, useState } from 'react';
import { Shield, RefreshCw, AlertTriangle, Users, Activity } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { MetricCard } from '@/components/ui/metric-card';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type AuditLog = {
  id: number;
  user_id?: number | null;
  user_name?: string | null;
  user_role?: string | null;
  method: string;
  path: string;
  entity_type?: string | null;
  entity_id?: number | null;
  status_code?: number | null;
  ip_address?: string | null;
  description?: string | null;
  created_at?: string | null;
};

/* ── Translations ─────────────────────────────────────────────────────────── */

const METHOD_LABEL: Record<string, string> = {
  GET: 'Consulta', POST: 'Criação', PUT: 'Edição', PATCH: 'Edição', DELETE: 'Exclusão',
};

const METHOD_COLOR: Record<string, string> = {
  GET:    'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  POST:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400',
  PUT:    'bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400',
  PATCH:  'bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400',
  DELETE: 'bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-400',
};

const ENTITY_LABEL: Record<string, string> = {
  autenticacao: 'Autenticação', cliente: 'Cliente', veiculo: 'Veículo',
  rastreador: 'Rastreador', contrato: 'Contrato', cobranca: 'Cobrança',
  plano: 'Plano', produto: 'Produto', servico: 'Serviço', ordem: 'Ordem de Serviço',
  usuario: 'Usuário', integracao: 'Integração', relatorio: 'Relatório',
  auditoria: 'Auditoria',
};

const ROLE_LABEL: Record<string, string> = {
  admin: 'Administrador', operacional: 'Operacional',
  financeiro: 'Financeiro', cliente: 'Cliente',
};

function friendlyEntity(type?: string | null) {
  if (!type) return null;
  const key = type.toLowerCase();
  return ENTITY_LABEL[key] ?? (type.charAt(0).toUpperCase() + type.slice(1));
}

function formatDate(ts?: string | null): { date: string; time: string } {
  if (!ts) return { date: '—', time: '' };
  const d = new Date(ts);
  const isToday = d.toDateString() === new Date().toDateString();
  return {
    date: isToday ? 'Hoje' : d.toLocaleDateString('pt-BR'),
    time: d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  };
}

function StatusIndicator({ code }: { code?: number | null }) {
  if (!code) return <span className="text-slate-300 dark:text-slate-600">—</span>;
  if (code < 300) return (
    <span
      className="inline-flex items-center text-xs font-semibold"
      style={{ background: '#EAF3DE', color: '#3B6D11', padding: '3px 10px', borderRadius: 20 }}
    >
      Sucesso
    </span>
  );
  if (code < 500) return (
    <span
      className="inline-flex items-center text-xs font-semibold"
      style={{ background: '#FAEEDA', color: '#854F0B', padding: '3px 10px', borderRadius: 20 }}
    >
      Recusado
    </span>
  );
  return (
    <span
      className="inline-flex items-center text-xs font-semibold"
      style={{ background: '#FCEBEB', color: '#A32D2D', padding: '3px 10px', borderRadius: 20 }}
    >
      Erro
    </span>
  );
}

const fieldClass = 'rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200';

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function AuditoriaPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['admin'], '/login/admin');

  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [methodFilter, setMethodFilter] = useState('');
  const [entityFilter, setEntityFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('');

  async function loadLogs(t: string) {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '500' });
      if (methodFilter) params.set('method', methodFilter);
      if (entityFilter) params.set('entity_type', entityFilter);
      if (roleFilter) params.set('user_role', roleFilter);
      const data = await apiFetch<AuditLog[]>(`/audit-logs?${params}`, {}, t);
      setLogs(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar registros.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (token) loadLogs(token);
  }, [token]);

  const filtered = useMemo(() => {
    if (!search.trim()) return logs;
    const q = search.trim().toLowerCase();
    return logs.filter(
      (l) =>
        l.description?.toLowerCase().includes(q) ||
        l.user_name?.toLowerCase().includes(q) ||
        l.path?.toLowerCase().includes(q) ||
        l.entity_type?.toLowerCase().includes(q),
    );
  }, [logs, search]);

  const entityTypes = useMemo(
    () => [...new Set(logs.map((l) => l.entity_type).filter(Boolean))].sort(),
    [logs],
  );

  const stats = useMemo(() => {
    const todayStr = new Date().toDateString();
    return {
      todayCount: logs.filter((l) => l.created_at && new Date(l.created_at).toDateString() === todayStr).length,
      errorCount: logs.filter((l) => l.status_code && l.status_code >= 400).length,
      uniqueUsers: new Set(logs.map((l) => l.user_id).filter(Boolean)).size,
    };
  }, [logs]);

  const pag = usePagination(filtered, 50);

  useEffect(() => { pag.reset(); }, [filtered.length]);

  return (
    <PageShell
      title="Auditoria"
      description="Registro completo de todas as ações realizadas por usuários autenticados no sistema."
    >
      {(guardError || error) && (
        <div className="mb-4">
          <ErrorBanner message={guardError || error} />
        </div>
      )}

      {/* ── KPIs ──────────────────────────────────────────────────────────── */}
      {logs.length > 0 && (
        <section className="mb-6 grid gap-4 sm:grid-cols-3">
          <MetricCard
            label="Ações hoje"
            value={stats.todayCount}
            sub={`${logs.length} registros carregados`}
            icon={<Activity className="h-5 w-5" />}
          />
          <MetricCard
            label="Usuários ativos"
            value={stats.uniqueUsers}
            sub="usuário(s) distintos no período"
            icon={<Users className="h-5 w-5" />}
          />
          <MetricCard
            label="Erros / Falhas"
            value={<span className={stats.errorCount > 0 ? 'text-red-600 dark:text-red-400' : ''}>{stats.errorCount}</span>}
            sub="respostas com erro (4xx / 5xx)"
            icon={<AlertTriangle className="h-5 w-5" />}
          />
        </section>
      )}

      <Card>
        {/* ── Filtros ─────────────────────────────────────────────────────── */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[180px] flex-1">
            <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Buscar</p>
            <input
              className={`${fieldClass} w-full`}
              placeholder="Usuário, ação, rota…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div>
            <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Tipo de operação</p>
            <select className={fieldClass} value={methodFilter} onChange={(e) => setMethodFilter(e.target.value)}>
              <option value="">Todas</option>
              <option value="GET">Consultas</option>
              <option value="POST">Criações</option>
              <option value="PUT">Edições</option>
              <option value="DELETE">Exclusões</option>
            </select>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Recurso</p>
            <select className={fieldClass} value={entityFilter} onChange={(e) => setEntityFilter(e.target.value)}>
              <option value="">Todos</option>
              {entityTypes.map((e) => (
                <option key={e} value={e!}>{friendlyEntity(e)}</option>
              ))}
            </select>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">Perfil</p>
            <select className={fieldClass} value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="">Todos</option>
              <option value="admin">Administrador</option>
              <option value="operacional">Operacional</option>
              <option value="financeiro">Financeiro</option>
              <option value="cliente">Cliente</option>
            </select>
          </div>

          <Button
            variant="secondary"
            onClick={() => token && loadLogs(token)}
            disabled={loading || guardLoading}
            className="gap-2 shrink-0"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
        </div>

        {/* ── Tabela ──────────────────────────────────────────────────────── */}
        <div className="mt-5">
          {loading || guardLoading ? (
            <TableSkeleton rows={10} cols={5} />
          ) : error ? (
            <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar a auditoria" description="Veja o erro acima e tente novamente." />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={Shield}
              title="Nenhum registro encontrado"
              description="Ajuste os filtros ou aguarde novas ações dos usuários."
            />
          ) : (
            <Table>
              <TableHead>
                <Th>Data / Hora</Th>
                <Th>Usuário</Th>
                <Th>Operação</Th>
                <Th>Recurso</Th>
                <Th>Resultado</Th>
                <Th>IP</Th>
              </TableHead>
              <TableBody>
                {pag.slice.map((log, rowIdx) => {
                  const dt = formatDate(log.created_at);
                  const isZebra = rowIdx % 2 === 1;
                  const isGroupSep = rowIdx > 0 && rowIdx % 5 === 0;
                  return (
                    <Tr
                      key={log.id}
                      className={[
                        isZebra ? 'bg-[#f9f9f9] dark:bg-slate-900/40' : '',
                        isGroupSep ? 'border-t border-slate-200 dark:border-slate-700' : '',
                      ].join(' ')}
                    >
                      {/* Data / Hora */}
                      <Td className="whitespace-nowrap px-4 py-[14px]">
                        <p className="text-[13px] font-medium text-slate-700 dark:text-slate-300">{dt.date}</p>
                        <p className="text-xs tabular-nums text-slate-400 dark:text-slate-500">{dt.time}</p>
                      </Td>

                      {/* Usuário + Perfil */}
                      <Td className="px-4 py-[14px]">
                        <p className="text-[13px] font-medium leading-tight text-slate-900 dark:text-white">
                          {log.user_name ?? '—'}
                        </p>
                        {log.user_role && (
                          <p className="mt-0.5 text-[11px] text-slate-400">
                            {ROLE_LABEL[log.user_role] ?? log.user_role}
                          </p>
                        )}
                      </Td>

                      {/* Operação: descrição + tag do tipo */}
                      <Td className="px-4 py-[14px]">
                        <p className="text-[13px] leading-snug text-slate-700 dark:text-slate-300">
                          {log.description ?? log.path}
                        </p>
                        {log.method && (
                          <span className={`mt-1 inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${METHOD_COLOR[log.method] ?? 'bg-slate-100 text-slate-500'}`}>
                            {METHOD_LABEL[log.method] ?? log.method}
                          </span>
                        )}
                      </Td>

                      {/* Recurso */}
                      <Td className="px-4 py-[14px]">
                        {log.entity_type ? (
                          <div>
                            <p className="text-[13px] text-slate-700 dark:text-slate-300">
                              {friendlyEntity(log.entity_type)}
                            </p>
                            {log.entity_id && (
                              <p className="text-[11px] text-slate-400">#{log.entity_id}</p>
                            )}
                          </div>
                        ) : (
                          <span className="text-slate-300 dark:text-slate-600">—</span>
                        )}
                      </Td>

                      {/* Resultado */}
                      <Td className="px-4 py-[14px]">
                        <StatusIndicator code={log.status_code} />
                      </Td>

                      {/* IP */}
                      <Td className="whitespace-nowrap px-4 py-[14px] text-[13px] tabular-nums text-slate-400">
                        {log.ip_address ?? '—'}
                      </Td>
                    </Tr>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>

        {filtered.length > 0 && (
          <Pagination
            page={pag.page}
            totalPages={pag.totalPages}
            total={pag.total}
            start={pag.start}
            end={pag.end}
            onPage={pag.setPage}
            className="mt-4"
          />
        )}
      </Card>
    </PageShell>
  );
}
