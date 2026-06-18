import Image from 'next/image';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';

export function HeroSection() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative overflow-hidden rounded-[28px] border border-[color:var(--fh-border)] bg-[color:var(--fh-surface)] px-6 py-14 backdrop-blur-xl sm:px-10 sm:py-20 lg:px-16 lg:py-24"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-40 -top-40 h-[420px] w-[420px] rounded-full bg-[color:var(--fh-accent-soft)] blur-[140px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-40 -left-40 h-[360px] w-[360px] rounded-full bg-white/[0.04] blur-[140px]"
      />

      <div className="relative">
        <div className="inline-flex items-center rounded-2xl bg-white px-4 py-2.5 shadow-lg shadow-black/30">
          <Image
            src="/logo.png"
            alt="MasterSat"
            width={183}
            height={58}
            className="h-8 w-auto object-contain sm:h-10"
            priority
          />
        </div>

        <h1
          id="hero-heading"
          className="mt-6 max-w-2xl text-[32px] font-bold leading-[1.15] tracking-tight text-[color:var(--fh-text-primary)] sm:text-[40px]"
        >
          Visibilidade total da sua operação logística, em tempo real
        </h1>

        <p className="mt-4 max-w-xl text-[16px] leading-relaxed text-[color:var(--fh-text-secondary)]">
          Centralize o rastreamento da frota, antecipe riscos e tome decisões orientadas por
          dados — uma plataforma única para times de logística e supply chain.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
          <Link
            href="/login/admin"
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[color:var(--fh-accent)] px-6 py-3 text-[14px] font-semibold text-slate-950 transition hover:-translate-y-0.5 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--fh-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--fh-bg)]"
          >
            Acessar plataforma
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
          <a
            href="#metricas"
            className="inline-flex items-center justify-center gap-2 rounded-2xl border border-[color:var(--fh-border)] px-6 py-3 text-[14px] font-semibold text-[color:var(--fh-text-primary)] transition hover:bg-[color:var(--fh-surface-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--fh-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--fh-bg)]"
          >
            Ver indicadores
          </a>
        </div>
      </div>
    </section>
  );
}
