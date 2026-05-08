import Link from 'next/link';
import { ReactNode } from 'react';
import { Radio, FileText, Wallet } from 'lucide-react';

const features = [
  {
    icon: Radio,
    title: 'Controle de ativos',
    description: 'Gerencie rastreadores, veículos e instalações em campo com histórico completo.',
  },
  {
    icon: FileText,
    title: 'Documentação integrada',
    description: 'Contratos, termos de instalação e recibos gerados e armazenados automaticamente.',
  },
  {
    icon: Wallet,
    title: 'Gestão financeira',
    description: 'Planos recorrentes, cobranças e relatórios de inadimplência centralizados.',
  },
];

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
    <main className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <div className="mx-auto grid min-h-screen max-w-7xl lg:grid-cols-[1fr_480px]">
        {/* Left — brand panel */}
        <section className="relative hidden flex-col justify-between overflow-hidden bg-brand-900 p-12 text-white lg:flex">
          {/* Subtle background blobs */}
          <div className="pointer-events-none absolute inset-0">
            <div className="absolute -left-24 -top-24 h-80 w-80 rounded-full bg-brand-700/60 blur-3xl" />
            <div className="absolute bottom-0 right-0 h-64 w-64 rounded-full bg-white/5 blur-3xl" />
          </div>

          {/* Logo */}
          <div className="relative">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/15">
                <Radio className="h-5 w-5 text-white" />
              </div>
              <span className="text-sm font-bold tracking-wide">Mastersat</span>
            </div>
          </div>

          {/* Headline */}
          <div className="relative space-y-6">
            <h1 className="text-4xl font-semibold leading-tight tracking-tight xl:text-5xl">
              Gestão completa de rastreamento veicular
            </h1>
            <p className="max-w-md text-base leading-7 text-white/70">
              Controle clientes, veículos, rastreadores, ordens de serviço e financeiro em uma única plataforma integrada.
            </p>

            <div className="space-y-4 pt-4">
              {features.map((f) => {
                const Icon = f.icon;
                return (
                  <div key={f.title} className="flex items-start gap-4">
                    <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10">
                      <Icon className="h-4 w-4 text-white/80" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold">{f.title}</p>
                      <p className="mt-0.5 text-sm text-white/60">{f.description}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Back link */}
          <div className="relative">
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/5 px-4 py-2.5 text-sm font-medium text-white/80 transition hover:bg-white/10 hover:text-white"
            >
              ← Voltar para a página inicial
            </Link>
          </div>
        </section>

        {/* Right — form panel */}
        <section className="flex items-center justify-center border-l border-slate-200 bg-white px-8 py-12 dark:border-slate-800 dark:bg-slate-900">
          <div className="w-full max-w-sm">
            {/* Mobile logo */}
            <div className="mb-8 flex items-center gap-2 lg:hidden">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-700">
                <Radio className="h-4 w-4 text-white" />
              </div>
              <span className="text-sm font-bold text-slate-900 dark:text-white">Mastersat</span>
            </div>

            <div className="mb-8">
              <span className="inline-block rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-brand-600 dark:bg-brand-900/40 dark:text-brand-300">
                {roleLabel}
              </span>
              <h2 className="mt-4 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{subtitle}</p>
            </div>

            {children}
          </div>
        </section>
      </div>
    </main>
  );
}
