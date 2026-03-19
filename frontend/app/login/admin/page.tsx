'use client';

import { FormEvent, useState } from 'react';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import { AuthUser, redirectByRole, saveSession } from '@/lib/auth';

type LoginResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

export default function AdminLoginPage() {
  const [email, setEmail] = useState('admin@rastreamento.local');
  const [password, setPassword] = useState('Admin@123');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');

    try {
      const result = await apiFetch<LoginResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      saveSession(result.access_token, result.refresh_token);
      const me = await apiFetch<AuthUser>('/auth/me', {}, result.access_token);
      redirectByRole(me);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Falha ao realizar o login.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Login administrativo"
      subtitle="Use o administrador inicial para testar a base ou entre com um usuário operacional/financeiro."
      roleLabel="Administrativo"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-700">E-mail</span>
          <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-700">Senha</span>
          <input type="password" className="w-full rounded-xl border border-slate-300 px-4 py-3" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <a href="/esqueci-senha" className="text-sm font-medium text-brand-500">
            Esqueci minha senha
          </a>
          <Button type="submit" disabled={loading}>
            {loading ? 'Entrando...' : 'Acessar painel'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
