'use client';

import { useEffect, useMemo, useState } from 'react';
import { MessageSquareText, Save } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { SectionHeader } from '@/components/ui/section-header';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type Mensagens = { msg_boleto: string; msg_boleto_assunto: string };

// Variáveis substituídas na hora do envio
const VARIAVEIS: { tag: string; desc: string }[] = [
  { tag: '{NOME}', desc: 'Nome do cliente' },
  { tag: '{VALOR}', desc: 'Valor da cobrança (ex.: 129,98)' },
  { tag: '{VENCIMENTO}', desc: 'Data de vencimento (ex.: 15/07/2026)' },
  { tag: '{REFERENTE}', desc: 'Mês de referência (ex.: 06/2026)' },
  { tag: '{CODIGO_BARRAS}', desc: 'Linha digitável (só números)' },
  { tag: '{LINK_BOLETO}', desc: 'Link público do boleto em PDF' },
];

const EXEMPLO: Record<string, string> = {
  NOME: 'CARLOS ROBERTO GUERRA',
  VALOR: '129,98',
  VENCIMENTO: '15/07/2026',
  REFERENTE: '06/2026',
  CODIGO_BARRAS: '08591020064004547020600000030125148900000129980',
  LINK_BOLETO: 'https://api.mastersat.com.br/api/v1/public/boleto/3/a1b2c3…',
};

export function renderTemplate(tpl: string, vars: Record<string, string>) {
  return tpl.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? `{${k}}`);
}

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500';

export default function ConfiguracoesPage() {
  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['admin'], '/login/admin');

  const [form, setForm] = useState<Mensagens>({ msg_boleto: '', msg_boleto_assunto: '' });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    if (!token) return;
    apiFetch<Mensagens>('/settings/mensagens', {}, token)
      .then(setForm)
      .catch((err) => setError(err instanceof Error ? err.message : 'Erro ao carregar'))
      .finally(() => setLoading(false));
  }, [token]);

  const preview = useMemo(() => renderTemplate(form.msg_boleto, EXEMPLO), [form.msg_boleto]);

  async function save() {
    if (!token) return;
    setSaving(true);
    setFeedback('');
    setError('');
    try {
      const saved = await apiFetch<Mensagens>('/settings/mensagens', {
        method: 'PUT',
        body: JSON.stringify(form),
      }, token);
      setForm(saved);
      setFeedback('Mensagens salvas — os próximos envios já usam o novo texto.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao salvar');
    } finally {
      setSaving(false);
    }
  }

  function inserirVariavel(tag: string) {
    setForm((p) => ({ ...p, msg_boleto: `${p.msg_boleto}${p.msg_boleto.endsWith('\n') || p.msg_boleto === '' ? '' : ' '}${tag}` }));
  }

  return (
    <PageShell title="Configurações" description="Textos e parâmetros do sistema editáveis sem mexer em código.">
      {(guardError || error) && <div className="mb-4"><ErrorBanner message={guardError || error} /></div>}
      {feedback && <p className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
      {guardLoading || loading ? (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Carregando…</p>
      ) : (
        <section className="grid gap-6 lg:grid-cols-2">
          <Card>
            <SectionHeader
              eyebrow="Mensagens ao cliente"
              title="Envio de boleto (WhatsApp e e-mail)"
              actions={
                <Button onClick={save} disabled={saving} className="gap-2">
                  <Save className="h-4 w-4" /> {saving ? 'Salvando…' : 'Salvar'}
                </Button>
              }
            />
            <div className="mt-4 space-y-4">
              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Assunto do e-mail</p>
                <input
                  className={fieldClass}
                  value={form.msg_boleto_assunto}
                  onChange={(e) => setForm((p) => ({ ...p, msg_boleto_assunto: e.target.value }))}
                />
              </div>
              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Mensagem (WhatsApp e corpo do e-mail)</p>
                <textarea
                  className={`${fieldClass} min-h-[280px] font-mono text-[13px] leading-relaxed`}
                  value={form.msg_boleto}
                  onChange={(e) => setForm((p) => ({ ...p, msg_boleto: e.target.value }))}
                />
              </div>
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Variáveis disponíveis (clique para inserir)</p>
                <div className="flex flex-wrap gap-2">
                  {VARIAVEIS.map(({ tag, desc }) => (
                    <button
                      key={tag}
                      type="button"
                      title={desc}
                      onClick={() => inserirVariavel(tag)}
                      className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 font-mono text-xs font-semibold text-brand-700 transition hover:bg-brand-100 dark:border-brand-900/40 dark:bg-brand-950/30 dark:text-brand-400"
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <SectionHeader eyebrow="Pré-visualização" title="Como o cliente vai receber" />
            <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/20">
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                <MessageSquareText className="h-4 w-4" /> WhatsApp (dados de exemplo)
              </div>
              <pre className="whitespace-pre-wrap break-words font-sans text-sm text-slate-800 dark:text-slate-200">{preview}</pre>
            </div>
            <p className="mt-3 text-xs text-slate-400">
              Assunto do e-mail: <span className="font-medium text-slate-600 dark:text-slate-300">{renderTemplate(form.msg_boleto_assunto, EXEMPLO)}</span>
            </p>
          </Card>
        </section>
      )}
    </PageShell>
  );
}
