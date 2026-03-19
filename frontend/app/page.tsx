import Link from 'next/link';

export default function Home() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-brand-900 via-brand-700 to-slate-950 px-6 py-10 text-white">
      <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[1.2fr_0.8fr]">
        <section className="rounded-[32px] border border-white/10 bg-white/5 p-10 shadow-2xl backdrop-blur">
          <p className="mb-3 text-sm uppercase tracking-[0.3em] text-slate-300">Sistema Full Stack</p>
          <h1 className="mb-4 text-5xl font-bold leading-tight">Gestão para rastreamento veicular com portal administrativo e portal do cliente.</h1>
          <p className="mb-8 max-w-2xl text-lg text-slate-200">
            Base em FastAPI, Next.js, PostgreSQL, Redis e MinIO pronta para evoluir os módulos operacional,
            financeiro e a jornada do cliente final.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/login/admin" className="rounded-xl bg-white px-5 py-3 font-semibold text-brand-900">
              Login ADM
            </Link>
            <Link href="/login/cliente" className="rounded-xl border border-white/20 px-5 py-3 font-semibold text-white">
              Login Cliente
            </Link>
            <Link href="/cadastro/cliente" className="rounded-xl border border-white/20 px-5 py-3 font-semibold text-white">
              Cadastrar Cliente
            </Link>
          </div>
        </section>

        <section className="rounded-[32px] bg-white p-8 text-slate-900 shadow-2xl">
          <p className="mb-2 text-sm font-semibold uppercase tracking-[0.2em] text-brand-500">Entregue nesta etapa</p>
          <h2 className="mb-4 text-3xl font-bold">Nova jornada de acesso</h2>
          <div className="space-y-4 text-sm text-slate-600">
            <div className="rounded-2xl border border-slate-200 p-4">
              <p className="font-semibold text-slate-900">Portal ADM</p>
              <p>Entrada separada para administradores, operação e financeiro.</p>
            </div>
            <div className="rounded-2xl border border-slate-200 p-4">
              <p className="font-semibold text-slate-900">Portal Cliente</p>
              <p>Cadastro completo com validação de CPF/CNPJ, telefone, CEP e força de senha.</p>
            </div>
            <div className="rounded-2xl border border-slate-200 p-4">
              <p className="font-semibold text-slate-900">Recuperação de senha</p>
              <p>Fluxo com solicitação de token e redefinição de senha.</p>
            </div>
          </div>
          <a href="http://localhost:8000/docs" className="mt-6 inline-flex rounded-xl bg-brand-900 px-4 py-3 font-semibold text-white">
            Ver documentação da API
          </a>
        </section>
      </div>
    </main>
  );
}
