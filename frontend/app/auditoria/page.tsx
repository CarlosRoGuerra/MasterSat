'use client';

import { useEffect, useMemo, useState } from 'react';
import { ClipboardList, RefreshCw } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
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

function parseError(e: unknown) {
  return e instanceof Error ? e.message : 'Erro inesperado.';
}

function methodBadge(method: string) {
  const variants: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'default'> = {
    GET: 'default',
    POST: 'success',
    PUT: 'warning',
    PATCH: 'warning',
    DELETE: 'danger',
  };
  return variants[method] ?? 'default';
}

function statusCodeVariant(code?: number | null) {
  if (!code) return 'default' as const;
  if (code < 300) return 'success' as const;
  if (code < 400) return 'info' as const;
  if (code < 500) return 'warning' as const;
  return 'danger' as const;
}

function roleLabel(role?: string | null) {
  const map: Record<string, string> = { admin: 'Administrador', operacional: 'Operacional', financeiro: 'Financeiro', cliente: 'Cliente' };
  return role ? (map[role] ?? role) : '—';
}

function formatDate(ts?: string | null) {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'medium' });
}

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
      const params = new URLSearchParams({ limit: '200' });
      if (methodFilter) params.set('method', methodFilter);
      if (entityFilter) params.set('entity_type', entityFilter);
      if (roleFilter) params.set('user_role', roleFilter);
      if (search.trim()) params.set('search', search.trim());
      const data = await apiFetch<AuditLog[]>(`/audit-logs?${params}`, {}, t);
      setLogs(data);
    } catch (e) {
      setError(parseError(e));
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

  const entityTypes = useMemo(() => [...new Set(logs.map((l) => l.entity_type).filter(Boolean))].sort(), [logs]);

  return (
    <PageShell
      title="Auditoria"
      description="Registro completo de todas as ações realizadas por usuários autenticados no sistema."
    >
      {(guardError || error) && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
          {guardError || error}
        </div>
      )}

      <Card>
        <div className="flex flex-wrap gap-3">
          <Input
            placeholder="Buscar por descrição, usuário ou rota…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-xs"
          />
          <Select value={methodFilter} onChange={(e) => setMethodFilter(e.target.value)} className="w-36">
            <option value="">Todos os métodos</option>
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="DELETE">DELETE</option>
          </Select>
          <Select value={entityFilter} onChange={(e) => setEntityFilter(e.target.value)} className="w-44">
            <option value="">Todas as entidades</option>
            {entityTypes.map((e) => <option key={e} value={e!}>{e}</option>)}
          </Select>
          <Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} className="w-44">
            <option value="">Todos os perfis</option>
            <option value="admin">Administrador</option>
            <option value="operacional">Operacional</option>
            <option value="financeiro">Financeiro</option>
            <option value="cliente">Cliente</option>
          </Select>
          <Button
            variant="secondary"
            onClick={() => token && loadLogs(token)}
            disabled={loading || guardLoading}
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
        </div>

        <div className="mt-4">
          {loading || guardLoading ? (
            <TableSkeleton rows={10} cols={6} />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={ClipboardList}
              title="Nenhum registro encontrado"
              description="Ajuste os filtros ou aguarde novas ações dos usuários."
            />
          ) : (
            <Table>
              <TableHead>
                <Th>Data/hora</Th>
                <Th>Usuário</Th>
                <Th>Perfil</Th>
                <Th>Ação</Th>
                <Th>Entidade</Th>
                <Th>Status</Th>
                <Th>IP</Th>
              </TableHead>
              <TableBody>
                {filtered.map((log) => (
                  <Tr key={log.id}>
                    <Td className="whitespace-nowrap text-xs text-slate-500">
                      {formatDate(log.created_at)}
                    </Td>
                    <Td>
                      <p className="font-medium text-slate-900 dark:text-white">{log.user_name ?? '—'}</p>
                    </Td>
                    <Td>
                      <span className="text-xs text-slate-500">{roleLabel(log.user_role)}</span>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant={methodBadge(log.method)}>{log.method}</Badge>
                        <span className="text-xs text-slate-600 dark:text-slate-300">{log.description ?? log.path}</span>
                      </div>
                    </Td>
                    <Td>
                      {log.entity_type ? (
                        <span className="text-xs text-slate-500">
                          {log.entity_type}{log.entity_id ? ` #${log.entity_id}` : ''}
                        </span>
                      ) : '—'}
                    </Td>
                    <Td>
                      {log.status_code ? (
                        <Badge variant={statusCodeVariant(log.status_code)}>{log.status_code}</Badge>
                      ) : '—'}
                    </Td>
                    <Td className="text-xs text-slate-400">{log.ip_address ?? '—'}</Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {filtered.length > 0 && (
          <p className="mt-3 text-right text-xs text-slate-400">
            {filtered.length} registro(s) exibido(s)
          </p>
        )}
      </Card>
    </PageShell>
  );
}
