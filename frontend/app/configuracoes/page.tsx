'use client';

import { useEffect, useMemo, useState } from 'react';
import { MessageSquareText, Save, Send } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { SectionHeader } from '@/components/ui/section-header';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type Mensagens = { msg_boleto: string; msg_boleto_assunto: string };
type EmailConfig = {
  host: string; port: number; username: string; from_email: string;
  from_name: string; security: string; enabled: boolean; password_set: boolean;
};
const emailVazio: EmailConfig = { host: '', port: 587, username: '', from_email: '', from_name: '', security: 'tls', enabled: false, password_set: false };

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

// Páginas Next só podem exportar default/metadata — helper fica interno
function renderTemplate(tpl: string, vars: Record<string, string>) {
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

  const [email, setEmail] = useState<EmailConfig>(emailVazio);
  const [emailPassword, setEmailPassword] = useState('');
  const [savingEmail, setSavingEmail] = useState(false);
  const [testingEmail, setTestingEmail] = useState(false);

  useEffect(() => {
    if (!token) return;
    apiFetch<Mensagens>('/settings/mensagens', {}, token)
      .then(setForm)
      .catch((err) => setError(err instanceof Error ? err.message : 'Erro ao carregar'))
      .finally(() => setLoading(false));
    apiFetch<EmailConfig>('/settings/email', {}, token).then(setEmail).catch(() => { /* opcional */ });
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

  async function saveEmail() {
    if (!token) return;
    setSavingEmail(true); setFeedback(''); setError('');
    try {
      // Só manda a senha quando o operador digitou uma nova; senão mantém a atual.
      const body = { ...email, password: emailPassword || null };
      const saved = await apiFetch<EmailConfig>('/settings/email', { method: 'PUT', body: JSON.stringify(body) }, token);
      setEmail(saved); setEmailPassword('');
      setFeedback('Configuração de e-mail salva.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao salvar o e-mail');
    } finally {
      setSavingEmail(false);
    }
  }

  async function testEmail() {
    if (!token) return;
    const to = window.prompt('Enviar e-mail de teste para qual endereço?', email.from_email || '');
    if (!to) return;
    setTestingEmail(true); setFeedback(''); setError('');
    try {
      const r = await apiFetch<{ message: string }>('/settings/email/test', { method: 'POST', body: JSON.stringify({ to }) }, token);
      setFeedback(r.message || 'E-mail de teste enviado.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao enviar o teste');
    } finally {
      setTestingEmail(false);
    }
  }

  return (
    <PageShell title="Configurações" description="Textos e parâmetros do sistema editáveis sem mexer em código.">
      {(guardError || error) && <div className="mb-4"><ErrorBanner message={guardError || error} /></div>}
      {feedback && <p className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
      {guardLoading || loading ? (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Carregando…</p>
      ) : (
        <div className="space-y-6">
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

        {/* ── Servidor de e-mail (SMTP) ─────────────────────────────────── */}
        <Card>
          <SectionHeader
            eyebrow="Envio de e-mail (SMTP)"
            title="Servidor de e-mail do sistema"
            actions={
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={testEmail} disabled={testingEmail || !email.host} className="gap-2">
                  <Send className="h-4 w-4" /> {testingEmail ? 'Enviando…' : 'Enviar teste'}
                </Button>
                <Button onClick={saveEmail} disabled={savingEmail} className="gap-2">
                  <Save className="h-4 w-4" /> {savingEmail ? 'Salvando…' : 'Salvar'}
                </Button>
              </div>
            }
          />
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="flex items-center gap-2 rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm dark:border-slate-700 md:col-span-2">
              <input type="checkbox" className="h-4 w-4 accent-brand-700" checked={email.enabled} onChange={(e) => setEmail((p) => ({ ...p, enabled: e.target.checked }))} />
              Ativar o envio de e-mail pelo sistema
            </label>

            <div className="md:col-span-2">
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Servidor SMTP</p>
              <input className={fieldClass} placeholder="smtp.seudominio.com.br" value={email.host} onChange={(e) => setEmail((p) => ({ ...p, host: e.target.value }))} />
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Porta</p>
              <input className={fieldClass} inputMode="numeric" value={email.port} onChange={(e) => setEmail((p) => ({ ...p, port: Number(e.target.value.replace(/\D/g, '')) || 0 }))} />
            </div>
            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Segurança</p>
              <select className={fieldClass} value={email.security} onChange={(e) => setEmail((p) => ({ ...p, security: e.target.value }))}>
                <option value="tls">STARTTLS (porta 587)</option>
                <option value="ssl">SSL/TLS (porta 465)</option>
                <option value="none">Nenhuma (sem criptografia)</option>
              </select>
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Usuário</p>
              <input className={fieldClass} placeholder="contato@seudominio.com.br" value={email.username} onChange={(e) => setEmail((p) => ({ ...p, username: e.target.value }))} autoComplete="off" />
            </div>
            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Senha</p>
              <input
                type="password"
                className={fieldClass}
                placeholder={email.password_set ? '•••••••• (mantém a atual)' : 'senha do e-mail'}
                value={emailPassword}
                onChange={(e) => setEmailPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">E-mail remetente</p>
              <input className={fieldClass} placeholder="contato@seudominio.com.br" value={email.from_email} onChange={(e) => setEmail((p) => ({ ...p, from_email: e.target.value }))} />
            </div>
            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400">Nome do remetente</p>
              <input className={fieldClass} placeholder="MasterSat Rastreamento" value={email.from_name} onChange={(e) => setEmail((p) => ({ ...p, from_name: e.target.value }))} />
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            A senha é guardada criptografada e nunca é exibida. O teste usa a configuração <strong>salva</strong> — salve antes de enviar o teste.
          </p>
        </Card>
        </div>
      )}
    </PageShell>
  );
}
