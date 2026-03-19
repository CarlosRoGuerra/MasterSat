import Link from 'next/link';
import { ReactNode } from 'react';

export function AuthShell({
  title,
  subtitle,
  children,
  roleLabel,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  roleLabel: string;
}) {
  return (
    <main className="min-h-screen bg-slate-100 px-4 py-10">
      <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-[32px] bg-brand-900 p-10 text-white shadow-2xl">
          <p className="mb-4 inline-flex rounded-full border border-white/20 px-3 py-1 text-xs uppercase tracking-[0.25em] text-slate-200">
            Portal {roleLabel}
          </p>
          <h1 className="mb-4 text-4xl font-bold leading-tight">Sistema de rastreamento veicular com acesso por perfil.</h1>
          <p className="max-w-xl text-slate-200">
            Acesse o ambiente administrativo ou o portal do cliente com uma experiência separada para cada tipo de usuário.
          </p>
          <div className="mt-8 space-y-3 text-sm text-slate-200">
            <p>• Cadastro com validação de CPF/CNPJ, telefone, CEP e força de senha.</p>
            <p>• Recuperação de senha com token de redefinição.</p>
            <p>• Dashboard do cliente com dados cadastrais, veículos e cobranças.</p>
          </div>
          <div className="mt-10 flex flex-wrap gap-3">
            <Link href="/" className="rounded-xl bg-white px-4 py-3 font-semibold text-brand-900">
              Voltar para a home
            </Link>
            <Link href="/login/admin" className="rounded-xl border border-white/20 px-4 py-3 font-semibold">
              Login ADM
            </Link>
            <Link href="/login/cliente" className="rounded-xl border border-white/20 px-4 py-3 font-semibold">
              Login Cliente
            </Link>
          </div>
        </section>

        <section className="rounded-[32px] bg-white p-8 shadow-xl">
          <p className="mb-2 text-sm font-semibold uppercase tracking-[0.2em] text-brand-500">{roleLabel}</p>
          <h2 className="mb-2 text-3xl font-bold text-slate-900">{title}</h2>
          <p className="mb-6 text-sm text-slate-500">{subtitle}</p>
          {children}
        </section>
      </div>
    </main>
  );
}
