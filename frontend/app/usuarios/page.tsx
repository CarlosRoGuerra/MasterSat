'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { ShieldCheck, Plus, Pencil, Trash2, RefreshCw, AlertTriangle } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';

type InternalRole = 'admin' | 'operacional' | 'financeiro';
type UserItem = { id: number; name: string; email: string; role: InternalRole | 'cliente'; active: boolean };
type UserForm = { name: string; email: string; role: InternalRole; password: string; active: boolean };

const initialForm: UserForm = { name: '', email: '', role: 'operacional', password: '', active: true };

function parseError(e: unknown) { return e instanceof Error ? e.message : 'Erro inesperado.'; }

const roleLabel: Record<string, string> = { admin: 'Administrador', operacional: 'Operacional', financeiro: 'Financeiro' };

export default function UsersPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/usuarios'], '/login/admin');
  const [users, setUsers] = useState<UserItem[]>([]);
  const [selected, setSelected] = useState<UserItem | null>(null);
  const [form, setForm] = useState<UserForm>(initialForm);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  async function loadUsers(t: string) {
    setLoading(true); setError('');
    try { setUsers((await apiFetch<UserItem[]>('/users', {}, t)).filter((u) => u.role !== 'cliente')); }
    catch (e) { setError(parseError(e)); }
    finally { setLoading(false); }
  }

  useEffect(() => { if (token) loadUsers(token); }, [token]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? users.filter((u) => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q) || u.role.includes(q)) : users;
  }, [users, search]);

  function reset() { setSelected(null); setForm(initialForm); }

  function startEdit(u: UserItem) {
    setSelected(u);
    setForm({ name: u.name, email: u.email, role: u.role === 'cliente' ? 'operacional' : u.role, password: '', active: u.active });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSaving(true); setError(''); setFeedback('');
    try {
      const payload = { name: form.name.trim(), email: form.email.trim().toLowerCase(), role: form.role, active: form.active, ...(form.password ? { password: form.password } : {}) };
      if (!payload.name) throw new Error('Informe o nome completo.');
      if (!payload.email) throw new Error('Informe o e-mail.');
      if (!selected && !form.password) throw new Error('Defina uma senha inicial.');
      if (selected) {
        await apiFetch(`/users/${selected.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Usuário atualizado com sucesso.');
      } else {
        await apiFetch('/users', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Usuário cadastrado com sucesso.');
      }
      reset(); await loadUsers(token);
    } catch (e) { setError(parseError(e)); }
    finally { setSaving(false); }
  }

  async function handleDelete(id: number) {
    if (!token || !window.confirm('Remover este usuário?')) return;
    setError(''); setFeedback('');
    try {
      await apiFetch(`/users/${id}`, { method: 'DELETE' }, token);
      setFeedback('Usuário removido.');
      if (selected?.id === id) reset();
      await loadUsers(token);
    } catch (e) { setError(parseError(e)); }
  }

  const field = (k: keyof UserForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  return (
    <PageShell
      title="Equipe administrativa"
      description="Gerencie administradores, operadores e perfis financeiros."
      actions={
        <Button onClick={reset} className="gap-2">
          <Plus className="h-4 w-4" /> Novo usuário
        </Button>
      }
    >
      {/* Feedback */}
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

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        {/* Lista */}
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar por nome, e-mail ou perfil…"
              className="max-w-xs"
            />
            <Button
              type="button"
              variant="secondary"
              onClick={() => token && loadUsers(token)}
              disabled={loading}
              className="gap-2"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Atualizar
            </Button>
          </div>

          {loading || guardLoading ? (
            <TableSkeleton rows={5} cols={4} />
          ) : error ? (
            <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os usuários" description="Veja o erro acima e tente novamente." />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="Nenhum usuário encontrado"
              description={search ? 'Tente outro termo de busca.' : 'Cadastre o primeiro usuário administrativo.'}
              action={<Button onClick={reset} className="gap-2"><Plus className="h-4 w-4" /> Novo usuário</Button>}
            />
          ) : (
            <Table>
              <TableHead>
                <Th>Usuário</Th>
                <Th>Perfil</Th>
                <Th>Status</Th>
                <Th className="w-24" />
              </TableHead>
              <TableBody>
                {filtered.map((u) => (
                  <Tr key={u.id} onClick={() => startEdit(u)} selected={selected?.id === u.id}>
                    <Td>
                      <p className="font-medium text-slate-900 dark:text-white">{u.name}</p>
                      <p className="text-xs text-slate-400">{u.email}</p>
                    </Td>
                    <Td>
                      <Badge variant="brand">{roleLabel[u.role] ?? u.role}</Badge>
                    </Td>
                    <Td>
                      <Badge variant={u.active ? 'success' : 'danger'}>
                        {u.active ? 'Ativo' : 'Inativo'}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1">
                        <button
                          onClick={(e) => { e.stopPropagation(); startEdit(u); }}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800"
                          title="Editar"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDelete(u.id); }}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"
                          title="Excluir"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {/* Formulário */}
        <Card>
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-brand-500 dark:text-brand-400">
                {selected ? 'Editar' : 'Novo'}
              </p>
              <h3 className="mt-1 text-base font-semibold text-slate-900 dark:text-white">
                {selected ? selected.name : 'Novo usuário'}
              </h3>
            </div>
            {selected && (
              <button
                onClick={reset}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
              >
                Cancelar
              </button>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <FormField label="Nome completo" required>
              <Input
                value={form.name}
                onChange={field('name')}
                placeholder="Nome e sobrenome"
                maxLength={120}
                required
              />
            </FormField>

            <FormField label="E-mail" required>
              <Input
                type="email"
                value={form.email}
                onChange={field('email')}
                placeholder="usuario@empresa.com"
                maxLength={160}
                required
              />
            </FormField>

            <FormGrid cols={2}>
              <FormField label="Perfil" required>
                <Select value={form.role} onChange={field('role')}>
                  <option value="admin">Administrador</option>
                  <option value="operacional">Operacional</option>
                  <option value="financeiro">Financeiro</option>
                </Select>
              </FormField>

              <FormField label={selected ? 'Nova senha (opcional)' : 'Senha inicial'} required={!selected}>
                <Input
                  type="password"
                  value={form.password}
                  onChange={field('password')}
                  placeholder={selected ? 'Deixe em branco para manter' : 'Defina uma senha segura'}
                  maxLength={80}
                />
              </FormField>
            </FormGrid>

            <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
              <input
                type="checkbox"
                checked={form.active}
                onChange={field('active')}
                className="h-4 w-4 rounded accent-brand-700"
              />
              <span className="text-sm text-slate-700 dark:text-slate-300">Usuário ativo</span>
            </label>

            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="secondary" onClick={reset}>Limpar</Button>
              <Button type="submit" disabled={saving}>
                {saving ? 'Salvando…' : selected ? 'Atualizar' : 'Cadastrar'}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </PageShell>
  );
}
