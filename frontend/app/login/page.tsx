import Link from 'next/link';

export default function LoginSelectorPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <div className="w-full max-w-3xl rounded-[32px] bg-white p-10 shadow-2xl">
        <p className="mb-2 text-sm font-semibold uppercase tracking-[0.2em] text-brand-500">Escolha o acesso</p>
        <h1 className="mb-3 text-4xl font-bold text-slate-900">Entrar no sistema</h1>
        <p className="mb-8 text-slate-500">Separe o acesso administrativo do portal do cliente para manter a experiência mais clara.</p>
        <div className="grid gap-4 md:grid-cols-2">
          <Link href="/login/admin" className="rounded-3xl border border-slate-200 p-6 transition hover:border-brand-500 hover:shadow-lg">
            <p className="mb-2 text-lg font-semibold text-slate-900">Login ADM</p>
            <p className="text-sm text-slate-500">Para administrador, operação e financeiro.</p>
          </Link>
          <Link href="/login/cliente" className="rounded-3xl border border-slate-200 p-6 transition hover:border-brand-500 hover:shadow-lg">
            <p className="mb-2 text-lg font-semibold text-slate-900">Login Cliente</p>
            <p className="text-sm text-slate-500">Para acompanhar cadastro, veículos e cobranças.</p>
          </Link>
        </div>
      </div>
    </main>
  );
}
