'use client';

import { FormEvent, Suspense, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormField } from '@/components/ui/form-field';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';
import { validatePassword } from '@/lib/format';

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const [token, setToken] = useState(searchParams.get('token') || '');
  const [newPassword, setNewPassword] = useState('');
  const [passwordConfirmation, setPasswordConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const passwordChecks = useMemo(() => validatePassword(newPassword), [newPassword]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    setMessage('');

    if (!Object.values(passwordChecks).every(Boolean)) {
      setError('A nova senha não atende aos critérios mínimos.');
      setLoading(false);
      return;
    }
    if (newPassword !== passwordConfirmation) {
      setError('As senhas não conferem.');
      setLoading(false);
      return;
    }

    try {
      const result = await apiFetch<{ message: string }>('/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({ token, new_password: newPassword, password_confirmation: passwordConfirmation }),
      });
      setMessage(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível redefinir a senha.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Redefinir senha"
      subtitle="Informe o token gerado e a nova senha de acesso."
      roleLabel="Acesso"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField label="Token">
          <Input value={token} onChange={(e) => setToken(e.target.value)} />
        </FormField>
        <FormField label="Nova senha">
          <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="••••••••" />
        </FormField>
        <FormField label="Confirmar nova senha">
          <Input type="password" value={passwordConfirmation} onChange={(e) => setPasswordConfirmation(e.target.value)} placeholder="••••••••" />
        </FormField>
        <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
          <p className="mb-2 font-semibold text-slate-900 dark:text-white">Regras da senha</p>
          <div className="grid gap-1 md:grid-cols-2">
            <p className={passwordChecks.minLength ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>• Mínimo de 8 caracteres</p>
            <p className={passwordChecks.upper ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>• Uma letra maiúscula</p>
            <p className={passwordChecks.lower ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>• Uma letra minúscula</p>
            <p className={passwordChecks.number ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>• Um número</p>
            <p className={passwordChecks.special ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>• Um caractere especial</p>
          </div>
        </div>
        {error && <ErrorBanner message={error} />}
        {message && (
          <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>
        )}
        <div className="flex justify-between gap-3">
          <Link
            href="/login/admin"
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Voltar ao login
          </Link>
          <Button type="submit" disabled={loading}>
            {loading ? 'Salvando...' : 'Salvar nova senha'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}
