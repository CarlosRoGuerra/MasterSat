'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { Copy, QrCode, RefreshCw } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatCurrency, formatDate } from '@/lib/format';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormField } from '@/components/ui/form-field';
import { Badge } from '@/components/ui/badge';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import type { Client } from './types';

type Charge = { id: number; billing_id: number; txid: string; status: string; vencimento: string;
  valor: string | number; pendencia: string | null; erro: string | null; enviada_em: string | null;
  inicio_janela_envio?: string; fim_janela_envio?: string };
type Data = {
  conexao: { configurado: boolean; escrita_habilitada: boolean; ambiente: string };
  recorrencia: { id: number; status: string; ativa: boolean; data_inicial: string; objeto: string;
    pix_copia_cola: string | null; qr_svg: string | null; erro: string | null } | null;
  cobrancas: Charge[]; pendentes_adesao: number;
  primeiro_vencimento_sugerido?: string; data_minima_autorizacao?: string;
};

const LABELS: Record<string, string> = {
  PREPARANDO: 'Preparando autorização', CRIADA: 'Aguardando autorização no banco', APROVADA: 'Autorizada pelo cliente',
  REJEITADA: 'Rejeitada', EXPIRADA: 'Expirada', CANCELADA: 'Cancelada', ERRO: 'Falha ao criar autorização',
  DESFECHO_DESCONHECIDO: 'Resultado pendente de consulta', NA_FILA: 'Aguardando janela de agendamento',
  AGUARDANDO_AUTORIZACAO: 'Aguardando autorização do cliente', ENVIANDO: 'Enviando agendamento',
  ATIVA: 'Agendada no banco', CONCLUIDA: 'Concluída no banco', PENDENCIA: 'Revisão necessária',
};
const TERMINAL = new Set(['CANCELADA', 'EXPIRADA', 'REJEITADA', 'CONCLUIDA']);

function primeiroVencimento(day?: number | null, ...suggestions: (string | null | undefined)[]): string {
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'America/Sao_Paulo' });
  const [year, month, date] = today.split('-').map(Number);
  const earliest = new Date(year, month - 1, date + 2);
  const minimum = `${earliest.getFullYear()}-${String(earliest.getMonth() + 1).padStart(2, '0')}-${String(earliest.getDate()).padStart(2, '0')}`;
  const suggested = suggestions.find((value) => value && /^\d{4}-\d{2}-\d{2}$/.test(value) && value >= minimum);
  if (suggested) return suggested;
  const dueDay = day || earliest.getDate();
  let due = new Date(year, month - 1, Math.min(dueDay, new Date(year, month, 0).getDate()));
  if (due < earliest) due = new Date(year, month, Math.min(dueDay, new Date(year, month + 1, 0).getDate()));
  return `${due.getFullYear()}-${String(due.getMonth() + 1).padStart(2, '0')}-${String(due.getDate()).padStart(2, '0')}`;
}

export function ClientPixAutomatico({ client, token, onUpdated, vencimentoCobranca }: {
  client: Pick<Client, 'id' | 'name' | 'billing_day'>; token: string; onUpdated?: () => void;
  vencimentoCobranca?: string | null;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const [initialDate, setInitialDate] = useState(() => primeiroVencimento(client.billing_day, vencimentoCobranca));
  const dateEdited = useRef(false);
  const [confirmation, setConfirmation] = useState<{ path: string; description: string } | null>(null);
  const base = `/sicredi-pix/clientes/${client.id}`;

  const load = useCallback(async () => {
    const response = await apiFetch<Data>(base, {}, token);
    setData(response);
  }, [base, token]);

  useEffect(() => {
    let active = true;
    apiFetch<Data>(base, {}, token).then((response) => { if (active) setData(response); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Falha ao carregar Pix Automático.'); });
    return () => { active = false; };
  }, [base, token]);

  useEffect(() => {
    if (!dateEdited.current && !data?.recorrencia) {
      setInitialDate(primeiroVencimento(client.billing_day, vencimentoCobranca, data?.primeiro_vencimento_sugerido));
    }
  }, [client.billing_day, vencimentoCobranca, data?.primeiro_vencimento_sugerido, data?.recorrencia]);

  const run = async (path: string, body?: object) => {
    setBusy(true); setError(''); setFeedback('');
    try {
      await apiFetch(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) }, token);
      await load();
      onUpdated?.();
      setFeedback('Informações do Pix Automático atualizadas.');
      setConfirmation(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível concluir a operação.');
      // Uma criação pode ter chegado ao banco: recuperar o estado salvo sem reenviar.
      await load().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const rec = data?.recorrencia;
  const enabled = Boolean(data?.conexao.escrita_habilitada);
  const canCreate = !rec || !rec.ativa;
  const minimumDate = data?.data_minima_autorizacao ?? primeiroVencimento();

  return (
    <section aria-label="Pix Automático" className="space-y-4 rounded-2xl border border-brand-200 bg-brand-50/30 p-4 dark:border-brand-900 dark:bg-brand-950/20">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><QrCode className="h-4 w-4" aria-hidden="true" />Pix Automático · Sicredi</h3>
          <p className="mt-1 text-xs text-slate-500">O cliente autoriza no banco. Cada cobrança mantém o vencimento definido no fechamento.</p>
        </div>
        {rec && <Badge variant={rec.status === 'APROVADA' ? 'success' : rec.ativa ? 'warning' : 'default'}>{LABELS[rec.status] ?? rec.status}</Badge>}
      </div>
      {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}
      {feedback && <p role="status" className="text-sm text-emerald-700">{feedback}</p>}
      {!data && !error && <p className="text-sm text-slate-500">Carregando Pix Automático…</p>}
      {data && !enabled && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">Autorizações e novos agendamentos ainda não estão liberados neste ambiente.</p>}
      {vencimentoCobranca && <p className="text-sm">Vencimento da cobrança selecionada: <strong>{formatDate(vencimentoCobranca)}</strong>.</p>}
      {data && canCreate && (
        <div className="flex flex-wrap items-end gap-3">
          <FormField label="Início da autorização" hint="Autorizar a recorrência não cria uma cobrança para esta data. O débito segue o vencimento de cada cobrança.">
            <Input type="date" min={minimumDate} value={initialDate} onChange={(event) => {
              dateEdited.current = true;
              setInitialDate(event.target.value);
            }} disabled={busy} />
          </FormField>
          <Button type="button" disabled={busy || !enabled || !initialDate || initialDate < minimumDate}
            onClick={() => run(`${base}/autorizacao`, { data_inicial: initialDate })}>Gerar autorização do cliente</Button>
        </div>
      )}
      {rec?.data_inicial && <p className="text-sm">Autorização iniciada em <strong>{formatDate(rec.data_inicial)}</strong>. A aprovação permite agendar as cobranças; ela não confirma pagamento nem cria uma cobrança nessa data.</p>}
      {rec?.erro && <p className="text-sm text-amber-800 dark:text-amber-200">{rec.erro}</p>}
      {rec?.ativa && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" disabled={busy || !data?.conexao.configurado} onClick={() => run(`${base}/consultar`)}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />Consultar autorização
          </Button>
          <Button type="button" variant="ghost" disabled={busy || !enabled} onClick={() => setConfirmation({
            path: `${base}/cancelar`, description: `Solicitar ao Sicredi o cancelamento da autorização de ${client.name}? Os agendamentos já enviados serão acompanhados até a confirmação do banco.`,
          })}>Cancelar autorização</Button>
        </div>
      )}
      {rec?.status === 'CRIADA' && rec.pix_copia_cola && (
        <div className="grid items-start gap-4 rounded-xl bg-white p-4 dark:bg-slate-900 sm:grid-cols-[160px_1fr]">
          {rec.qr_svg && <Image unoptimized src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(rec.qr_svg)}`} width={160} height={160} alt="QR Code para autorizar Pix Automático" className="rounded-lg bg-white p-2" />}
          <div className="space-y-3">
            <p className="text-sm">O cliente deve ler este QR Code ou colar o código no aplicativo do banco para autorizar as mensalidades e serviços da MasterSat.</p>
            <label className="block text-xs font-medium">Código de autorização
              <textarea readOnly value={rec.pix_copia_cola} rows={3} className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs dark:border-slate-700 dark:bg-slate-800" />
            </label>
            <Button type="button" variant="secondary" onClick={async () => {
              try { await navigator.clipboard.writeText(rec.pix_copia_cola!); setFeedback('Código de autorização copiado.'); }
              catch { setError('Não foi possível copiar. Selecione o código no campo acima.'); }
            }}><Copy className="h-4 w-4" aria-hidden="true" />Copiar código</Button>
          </div>
        </div>
      )}
      {Boolean(data?.pendentes_adesao) && <div className="space-y-2">
        <p className="text-sm">{data!.pendentes_adesao} cobrança(s) do fechamento aguardam uma autorização compatível com o vencimento.</p>
        {rec?.ativa && <Button type="button" variant="secondary" disabled={busy} onClick={() => run(`${base}/preparar-pendentes`)}>Preparar cobranças do fechamento</Button>}
      </div>}
      {Boolean(data?.cobrancas.length) && <div className="space-y-2">
        <h4 className="text-sm font-semibold">Agendamentos do fechamento</h4>
        {data!.cobrancas.map((charge) => <div key={charge.id} className="space-y-2 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>Cobrança #{charge.billing_id} · {formatDate(charge.vencimento)} · {formatCurrency(Number(charge.valor))}</span>
            <Badge variant={charge.pendencia ? 'warning' : 'default'}>{charge.status === 'CRIADA' ? 'Criada no banco' : LABELS[charge.status] ?? charge.status}</Badge>
          </div>
          {(charge.pendencia || charge.erro) && <p className="text-xs text-amber-800 dark:text-amber-200">{charge.pendencia || charge.erro}</p>}
          {!charge.enviada_em && !TERMINAL.has(charge.status) && <div className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
            <p>Débito previsto para {formatDate(charge.vencimento)}. Esta cobrança ainda não foi enviada ao banco.</p>
            {charge.inicio_janela_envio && charge.fim_janela_envio && <p>Janela de envio ao Sicredi: {formatDate(charge.inicio_janela_envio)} a {formatDate(charge.fim_janela_envio)}. O envio exige autorização aprovada.</p>}
          </div>}
          <div className="flex gap-2">
            {charge.enviada_em && <Button type="button" variant="ghost" disabled={busy || !data!.conexao.configurado} onClick={() => run(`/sicredi-pix/cobrancas/${charge.id}/consultar`)}>Consultar pagamento</Button>}
            {!TERMINAL.has(charge.status) && <Button type="button" variant="ghost" disabled={busy || (!!charge.enviada_em && !enabled)} onClick={() => setConfirmation({
              path: `/sicredi-pix/cobrancas/${charge.id}/cancelar`, description: `Cancelar o agendamento Pix da cobrança #${charge.billing_id}? A cobrança continuará no Financeiro para conferência.`,
            })}>Cancelar agendamento</Button>}
          </div>
        </div>)}
      </div>}
      <ConfirmDialog open={!!confirmation} title="Cancelar Pix Automático" description={confirmation?.description ?? ''}
        confirmLabel="Confirmar cancelamento" danger loading={busy} onClose={() => { if (!busy) setConfirmation(null); }}
        onConfirm={() => { if (confirmation) void run(confirmation.path); }} />
    </section>
  );
}
