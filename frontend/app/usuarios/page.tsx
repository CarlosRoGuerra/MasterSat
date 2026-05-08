'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type InternalRole = 'admin' | 'operacional' | 'financeiro';

type UserItem = {
  id: number;
  name: string;
  email: string;
  role: InternalRole | 'cliente';
  active: boolean;
};

type UserForm = {
  name: string;
  email: string;
  role: InternalRole;
  password: string;
  active: boolean;
};

const initialForm: UserForm = {
  name: '',
  email: '',
  role: 'operacional',
  password: '',
  active: true,
};

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export default function UsersPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['admin'], '/login/admin');
  const [users, setUsers] = useState<UserItem[]>([]);
  const [selectedUser, setSelectedUser] = useState<UserItem | null>(null);
  const [form, setForm] = useState<UserForm>(initialForm);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  async function loadUsers(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<UserItem[]>('/users', {}, currentToken);
      setUsers(response.filter((item) => item.role !== 'cliente'));
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadUsers(token);
  }, [token]);

  const filteredUsers = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return users;
    return users.filter((item) => item.name.toLowerCase().includes(term) || item.email.toLowerCase().includes(term) || item.role.includes(term));
  }, [users, search]);

  function resetForm() {
    setSelectedUser(null);
    setForm(initialForm);
  }

  function editUser(user: UserItem) {
    setSelectedUser(user);
    setForm({
      name: user.name,
      email: user.email,
      role: user.role === 'cliente' ? 'operacional' : user.role,
      password: '',
      active: user.active,
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setSaving(true);
    setError('');
    setFeedback('');
    try {
      const payload = {
        name: form.name.trim(),
        email: form.email.trim().toLowerCase(),
        role: form.role,
        active: form.active,
        ...(form.password ? { password: form.password } : {}),
      };

      if (!payload.name) throw new Error('Informe o nome completo do usuário.');
      if (!payload.email) throw new Error('Informe o e-mail do usuário.');
      if (!selectedUser && !form.password) throw new Error('Defina uma senha inicial para o novo administrador.');

      if (selectedUser) {
        await apiFetch(`/users/${selectedUser.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Usuário administrativo atualizado com sucesso.');
      } else {
        await apiFetch('/users', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Novo usuário administrativo cadastrado com sucesso.');
      }
      resetForm();
      await loadUsers(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(userId: number) {
    if (!token) return;
    if (!window.confirm('Deseja remover este usuário administrativo?')) return;
    setError('');
    setFeedback('');
    try {
      await apiFetch(`/users/${userId}`, { method: 'DELETE' }, token);
      setFeedback('Usuário removido com sucesso.');
      if (selectedUser?.id === userId) resetForm();
      await loadUsers(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Equipe administrativa">
      {(guardError || error || feedback) && (
        <div className="mb-6 space-y-3">
          {(guardError || error) && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{guardError || error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      {guardLoading && <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">Validando sessão...</p>}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Acessos internos</p>
              <h3 className="mt-2 text-2xl font-semibold text-slate-900">Administradores e perfis internos</h3>
              <p className="mt-2 text-sm text-slate-500">Cadastre novos administradores, operadores e perfis do financeiro com acesso ao sistema.</p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Usuários</p>
              <p className="mt-1 text-2xl font-semibold text-slate-900">{users.length}</p>
            </div>
          </div>

          <div className="mb-4 grid gap-4 md:grid-cols-[1fr_auto]">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700">Buscar na equipe</span>
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nome, e-mail ou perfil" className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm" />
            </label>
            <div className="flex items-end"><Button type="button" onClick={() => token && loadUsers(token)} disabled={loading}>{loading ? 'Atualizando...' : 'Atualizar'}</Button></div>
          </div>

          <div className="space-y-3">
            {filteredUsers.length === 0 ? <p className="text-sm text-slate-500">Nenhum usuário administrativo encontrado.</p> : filteredUsers.map((user) => (
              <div key={user.id} className="flex flex-wrap items-start justify-between gap-3 rounded-3xl border border-slate-200 bg-slate-50 p-4">
                <div>
                  <p className="font-semibold text-slate-900">{user.name}</p>
                  <p className="mt-1 text-sm text-slate-500">{user.email}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold uppercase text-slate-600">{user.role}</span>
                    <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${user.active ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'}`}>{user.active ? 'ativo' : 'inativo'}</span>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={() => editUser(user)}>Editar</Button>
                  <Button type="button" className="bg-red-600 hover:bg-red-700" onClick={() => handleDelete(user.id)}>Excluir</Button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Cadastro de acesso</p>
              <h3 className="mt-2 text-2xl font-semibold text-slate-900">{selectedUser ? 'Editar usuário' : 'Novo administrador'}</h3>
            </div>
            {selectedUser && <button type="button" className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700" onClick={resetForm}>Cancelar</button>}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Nome completo</span><input value={form.name} onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value.slice(0, 120) }))} className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm" maxLength={120} required /></label>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">E-mail</span><input type="email" value={form.email} onChange={(e) => setForm((prev) => ({ ...prev, email: e.target.value.slice(0, 160) }))} className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm" maxLength={160} required /></label>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Perfil</span><select value={form.role} onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value as InternalRole }))} className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm"><option value="admin">Administrador</option><option value="operacional">Operacional</option><option value="financeiro">Financeiro</option></select></label>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">{selectedUser ? 'Nova senha (opcional)' : 'Senha inicial'}</span><input type="password" value={form.password} onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value.slice(0, 80) }))} className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm" maxLength={80} placeholder={selectedUser ? 'Preencha apenas se quiser alterar' : 'Defina uma senha segura'} /></label>
            <label className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700"><input type="checkbox" checked={form.active} onChange={(e) => setForm((prev) => ({ ...prev, active: e.target.checked }))} /> Usuário ativo</label>
            <div className="flex justify-end gap-3 pt-2">
              <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={resetForm}>Limpar</Button>
              <Button type="submit" disabled={saving}>{saving ? 'Salvando...' : selectedUser ? 'Atualizar usuário' : 'Cadastrar usuário'}</Button>
            </div>
          </form>
        </Card>
      </div>
    </PageShell>
  );
}
