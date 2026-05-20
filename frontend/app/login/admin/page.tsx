'use client';

import { FormEvent, useState } from 'react';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormField } from '@/components/ui/form-field';
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
      title="Acesso administrativo"
      subtitle="Entre com seu e-mail e senha para acessar o painel de gestão."
      roleLabel="Administrativo"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField label="E-mail">
          <Input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="seu@email.com"
          />
        </FormField>

        <FormField label="Senha">
          <Input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </FormField>

        {error && (
          <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 pt-1">
          <a
            href="/esqueci-senha"
            className="text-sm text-slate-500 hover:text-brand-600 dark:text-slate-400 dark:hover:text-brand-400"
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
