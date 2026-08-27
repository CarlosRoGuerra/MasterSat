import Link from 'next/link';
import { Radio } from 'lucide-react';

export default function LoginSelectorPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12 dark:bg-slate-950">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="mb-10 flex flex-col items-center gap-3 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-700 shadow-lg shadow-brand-900/30">
            <Radio className="h-6 w-6 text-white" />
          </div>
          <div>
            <p className="text-xl font-bold text-slate-900 dark:text-white">Mastersat</p>
            <p className="text-sm text-slate-500 dark:text-slate-400">Sistema de gestão de rastreamento veicular</p>
          </div>
        </div>

        {/* Card */}
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel dark:border-slate-800 dark:bg-slate-900">
          <div className="border-b border-slate-100 px-6 py-5 dark:border-slate-800">
            <h1 className="text-base font-semibold text-slate-900 dark:text-white">Escolha seu tipo de acesso</h1>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Selecione o perfil de entrada para continuar.
            </p>
          </div>

          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            <Link
              href="/login/admin"
              className="flex items-center gap-4 px-6 py-5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 dark:bg-brand-900/30">
                <Radio className="h-5 w-5 text-brand-700 dark:text-brand-300" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-900 dark:text-white">Painel administrativo</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Acesso para administradores, operação e financeiro.
                </p>
              </div>
              <span className="ml-auto text-slate-300 dark:text-slate-600">→</span>
            </Link>

            <Link
              href="/login/cliente"
              className="flex items-center gap-4 px-6 py-5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800">
                <span className="text-lg">🚗</span>
              </div>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-900 dark:text-white">Portal do cliente</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Acompanhe seus veículos, rastreadores e cobranças.
                </p>
              </div>
              <span className="ml-auto text-slate-300 dark:text-slate-600">→</span>
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
