import Link from 'next/link';

const pillars = [
  {
    title: 'Ambiente administrativo único',
    description: 'Clientes, veículos, rastreadores, documentos e integração técnica concentrados em um único painel.',
  },
  {
    title: 'Cadastro centralizado pela equipe',
    description: 'O cliente não realiza mais cadastro público. Toda inclusão e manutenção cadastral ocorre pela área administrativa.',
  },
  {
    title: 'Infraestrutura preparada',
    description: 'FastAPI, Next.js, PostgreSQL, Redis e MinIO compondo uma base pronta para crescer.',
  },
];

const modules = [
  'Clientes cadastrados somente pelo administrativo, com documentos anexados no mesmo fluxo',
  'Veículos em padrão administrativo com documentos e validações operacionais',
  'Rastreadores com vínculo técnico, histórico e integração Multiportal',
  'Documentos armazenados no MinIO com acesso mediado pelo backend',
];

export default function Home() {
  return (
    <main className="min-h-screen px-4 py-6 lg:px-6 lg:py-8">
      <div className="mx-auto max-w-7xl">
        <section className="relative overflow-hidden rounded-[36px] bg-gradient-to-br from-brand-900 via-brand-700 to-slate-950 px-6 py-8 text-white shadow-[0_45px_140px_-58px_rgba(15,23,42,0.9)] lg:px-10 lg:py-12">
          <div className="absolute -left-16 top-0 h-56 w-56 rounded-full bg-cyan-300/10 blur-3xl" />
          <div className="absolute right-0 top-10 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
          <div className="relative grid gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
            <div>
              <p className="inline-flex rounded-full border border-white/20 bg-white/10 px-4 py-1.5 text-xs uppercase tracking-[0.32em] text-slate-100">
                Mastersat • Plataforma de gestão
              </p>
              <h1 className="mt-6 max-w-4xl text-4xl font-semibold leading-tight lg:text-6xl">
                Gestão moderna para operação e controle técnico de uma empresa de rastreamento veicular.
              </h1>
              <p className="mt-5 max-w-2xl text-base leading-8 text-slate-200 lg:text-lg">
                Uma base full stack focada em operação real, com o cadastro de clientes e ativos centralizado na equipe administrativa para garantir padrão, organização e controle.
              </p>

              <div className="mt-8 flex flex-wrap gap-3">
                <Link href="/login/admin" className="rounded-2xl bg-white px-5 py-3 text-sm font-semibold text-brand-900 shadow-lg shadow-black/10 transition hover:-translate-y-0.5">
                  Entrar no painel administrativo
                </Link>
              </div>

              <div className="mt-10 grid gap-4 sm:grid-cols-3">
                <div className="rounded-3xl border border-white/10 bg-white/10 p-4 backdrop-blur-sm">
                  <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Perfis internos</p>
                  <p className="mt-2 text-3xl font-semibold">3</p>
                  <p className="mt-1 text-sm text-slate-200">Administrador, operacional e financeiro.</p>
                </div>
                <div className="rounded-3xl border border-white/10 bg-white/10 p-4 backdrop-blur-sm">
                  <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Documentos</p>
                  <p className="mt-2 text-3xl font-semibold">MinIO</p>
                  <p className="mt-1 text-sm text-slate-200">Armazenamento seguro com mediação do backend.</p>
                </div>
                <div className="rounded-3xl border border-white/10 bg-white/10 p-4 backdrop-blur-sm">
                  <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Fluxo</p>
                  <p className="mt-2 text-3xl font-semibold">Central</p>
                  <p className="mt-1 text-sm text-slate-200">Cadastro de clientes e ativos realizado apenas pela equipe.</p>
                </div>
              </div>
            </div>

            <div className="grid gap-4">
              <div className="rounded-[30px] border border-white/10 bg-white/10 p-5 backdrop-blur-sm">
                <p className="text-xs uppercase tracking-[0.24em] text-slate-300">O que você tem nesta etapa</p>
                <div className="mt-4 space-y-3">
                  {pillars.map((pillar) => (
                    <div key={pillar.title} className="rounded-2xl border border-white/10 bg-black/10 p-4">
                      <p className="font-semibold">{pillar.title}</p>
                      <p className="mt-1 text-sm text-slate-200">{pillar.description}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div className="rounded-[30px] border border-white/10 bg-white/95 p-5 text-slate-900 shadow-2xl">
                <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand-500">Módulos já preparados</p>
                <ul className="mt-4 space-y-3 text-sm text-slate-600">
                  {modules.map((module) => (
                    <li key={module} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                      {module}
                    </li>
                  ))}
                </ul>
                <a
                  href="http://localhost:8000/docs"
                  className="mt-5 inline-flex rounded-2xl bg-brand-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Ver documentação da API
                </a>
              </div>
            </div>
          </div>
        </section>

        <section className="mt-8 grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
          <div className="rounded-[32px] border border-white/70 bg-white p-6 shadow-[0_24px_70px_-42px_rgba(15,23,42,0.35)] backdrop-blur">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand-500">Resumo do fluxo</p>
            <h2 className="mt-3 text-3xl font-semibold text-slate-900">Jornada administrativa centralizada</h2>
            <div className="mt-6 space-y-4 text-sm leading-7 text-slate-600">
              <p>
                O cadastro de clientes passa a ser realizado exclusivamente pela equipe administrativa, mantendo padrão documental e validação interna.
              </p>
              <p>
                O mesmo ambiente concentra cadastro e manutenção de veículos, rastreadores, documentos, integrações e gestão dos usuários internos.
              </p>
              <p>
                Isso reduz ruído operacional e mantém a base preparada para crescer para ordens de serviço, contratos e financeiro.
              </p>
            </div>
          </div>
          <div className="grid gap-5 md:grid-cols-2">
            <div className="rounded-[32px] border border-white/70 bg-white p-6 shadow-[0_24px_70px_-42px_rgba(15,23,42,0.35)] backdrop-blur">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand-500">Acesso rápido</p>
              <h3 className="mt-3 text-2xl font-semibold text-slate-900">Entrar no ambiente</h3>
              <div className="mt-5 space-y-3">
                <Link href="/login/admin" className="block rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 transition hover:border-brand-500 hover:bg-slate-100">
                  <p className="font-semibold text-slate-900">Painel administrativo</p>
                  <p className="mt-1 text-sm text-slate-500">Operação, financeiro, cadastros, equipe e visão completa da base.</p>
                </Link>
              </div>
            </div>
            <div className="rounded-[32px] border border-white/70 bg-white p-6 shadow-[0_24px_70px_-42px_rgba(15,23,42,0.35)] backdrop-blur">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand-500">Base visual</p>
              <h3 className="mt-3 text-2xl font-semibold text-slate-900">Interface profissional</h3>
              <p className="mt-4 text-sm leading-7 text-slate-600">
                A home, a entrada administrativa e os dashboards foram desenhados para transmitir mais organização, credibilidade e clareza no uso diário.
              </p>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
