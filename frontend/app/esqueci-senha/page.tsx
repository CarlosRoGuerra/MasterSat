'use client';

import { FormEvent, useState } from 'react';
import Link from 'next/link';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormField } from '@/components/ui/form-field';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';

type ForgotPasswordResponse = {
  message: string;
  reset_token?: string | null;
  expires_at?: string | null;
};

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    setMessage('');
    setToken('');

    try {
      const result = await apiFetch<ForgotPasswordResponse>('/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email }),
      });
      setMessage(result.message);
      setToken(result.reset_token || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível solicitar a redefinição.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Esqueci minha senha"
      subtitle="Informe seu e-mail para gerar um token de redefinição."
      roleLabel="Acesso"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField label="E-mail">
          <Input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value.toLowerCase())}
            placeholder="seu@email.com"
          />
        </FormField>
        {error && <ErrorBanner message={error} />}
        {message && (
          <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>
        )}
        {token && (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
            <p className="font-semibold">Ambiente de desenvolvimento</p>
            <p className="mt-1 break-all">Token: {token}</p>
            <Link href={`/resetar-senha?token=${token}`} className="mt-3 inline-flex font-semibold text-brand-700 hover:underline dark:text-brand-400">
              Redefinir agora
            </Link>
          </div>
        )}
        <div className="flex justify-between gap-3">
          <Link
            href="/login/admin"
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Voltar ao login
          </Link>
          <Button type="submit" disabled={loading}>
            {loading ? 'Enviando...' : 'Gerar token'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
