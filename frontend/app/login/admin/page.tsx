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

const inputClass =
  'w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 transition placeholder:text-slate-400 focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-800/60 dark:text-white dark:placeholder:text-slate-500';

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
      title="Acesso administrativo"
      subtitle="Entre com seu e-mail e senha para acessar o painel de gestão."
      roleLabel="Administrativo"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
            E-mail
          </span>
          <input
            type="email"
            autoComplete="email"
            className={inputClass}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="seu@email.com"
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
            Senha
          </span>
          <input
            type="password"
            autoComplete="current-password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        {error && (
          <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 pt-1">
          <a
            href="/esqueci-senha"
            className="text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
          >
            Esqueci minha senha
          </a>
          <Button type="submit" disabled={loading}>
            {loading ? 'Entrando…' : 'Entrar'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
