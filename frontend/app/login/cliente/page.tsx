'use client';

import { FormEvent, useState } from 'react';
import Link from 'next/link';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import { AuthUser, redirectByRole, saveSession } from '@/lib/auth';

type LoginResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

export default function ClientLoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
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
      title="Login do cliente"
      subtitle="Entre para acompanhar seu contrato, veículos e cobranças."
      roleLabel="Cliente"
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
          <div className="flex flex-wrap gap-3 text-sm">
            <Link href="/cadastro/cliente" className="font-medium text-brand-500">
              Criar conta
            </Link>
            <Link href="/esqueci-senha" className="font-medium text-brand-500">
              Esqueci minha senha
            </Link>
          </div>
          <Button type="submit" disabled={loading}>
            {loading ? 'Entrando...' : 'Acessar portal'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
