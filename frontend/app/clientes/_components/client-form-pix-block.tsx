'use client';

import { useEffect } from 'react';
import { QrCode } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatDate } from '@/lib/format';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { primeiroVencimento, ROTULO_AUTORIZACAO } from './client-pix-automatico';

/** ``solicitar: null`` = regra padrão (ver ``deveSolicitarPix``). */
export type PixCadastroOpcoes = { data_inicial: string; enviar_convite: boolean; solicitar: boolean | null };
export type PixCadastroContexto = {
  conexao: { configurado: boolean; escrita_habilitada: boolean; smtp_configurado?: boolean | null } | null;
  recorrencia: { status: string; ativa: boolean; data_inicial: string } | null;
  sugestaoServidor: string | null;
  erro?: string;
};

export const PIX_OPCOES_INICIAIS: PixCadastroOpcoes = { data_inicial: '', enviar_convite: true, solicitar: null };

/** Pedir a autorização ao salvar? Por padrão só quando Pix acabou de ser
 *  escolhido ou o cliente nunca teve autorização: editar um cadastro que já era
 *  Pix (ex.: trocar o telefone) não dispara pedido ao banco nem e-mail. */
export function deveSolicitarPix(opcoes: PixCadastroOpcoes, novaSelecao: boolean, contexto: PixCadastroContexto | null): boolean {
  if (contexto?.recorrencia?.ativa) return false;
  return opcoes.solicitar ?? (novaSelecao || !contexto?.recorrencia);
}

/** Mesma regra da tela de detalhes: cobrança real/servidor primeiro, depois o dia do cadastro. */
export function sugestaoInicioPix(billingDay: string, servidor?: string | null): string {
  const day = Number(billingDay);
  return primeiroVencimento(Number.isInteger(day) && day >= 1 && day <= 31 ? day : null, servidor);
}

/** Quadro compacto exibido no cadastro somente quando a forma é Pix Automático. */
export function ClientFormPixBlock({
  token, clientId, novaSelecao, canFinance, nome, email, billingDay, sugestao, opcoes, onOpcoesChange, contexto, onContextoChange,
}: {
  token: string | null;
  clientId: number | null;
  /** Pix escolhido agora (cliente novo ou forma anterior diferente). */
  novaSelecao: boolean;
  canFinance: boolean;
  nome: string;
  email: string;
  billingDay: string;
  sugestao: string;
  opcoes: PixCadastroOpcoes;
  onOpcoesChange: (opcoes: PixCadastroOpcoes) => void;
  contexto: PixCadastroContexto | null;
  onContextoChange: (contexto: PixCadastroContexto) => void;
}) {
  useEffect(() => {
    if (!token || !canFinance) return;
    let active = true;
    (async () => {
      try {
        const conexao = await apiFetch<PixCadastroContexto['conexao']>('/sicredi-pix/status', {}, token);
        let recorrencia: PixCadastroContexto['recorrencia'] = null;
        let sugestaoServidor: string | null = null;
        if (clientId) {
          const data = await apiFetch<{ recorrencia: PixCadastroContexto['recorrencia']; primeiro_vencimento_sugerido?: string }>(
            `/sicredi-pix/clientes/${clientId}`, {}, token);
          recorrencia = data.recorrencia;
          sugestaoServidor = data.primeiro_vencimento_sugerido ?? null;
        }
        if (active) onContextoChange({ conexao, recorrencia, sugestaoServidor });
      } catch (err) {
        if (active) onContextoChange({ conexao: null, recorrencia: null, sugestaoServidor: null,
          erro: err instanceof Error ? err.message : 'Não foi possível consultar o Pix Automático.' });
      }
    })();
    return () => { active = false; };
    // onContextoChange é um setState estável do container.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, clientId, canFinance]);

  const rec = contexto?.recorrencia;
  const ativa = Boolean(rec?.ativa);
  const minimo = primeiroVencimento();
  const valorData = opcoes.data_inicial || sugestao;
  const semEmail = !email.trim();
  const conexao = contexto?.conexao;
  const solicitar = deveSolicitarPix(opcoes, novaSelecao, contexto);

  return (
    <section aria-label="Pix Automático — Sicredi"
      className="space-y-3 rounded-2xl border border-brand-200 bg-brand-50/30 p-4 dark:border-brand-900 dark:bg-brand-950/20">
      <div>
        <h4 className="flex items-center gap-2 text-sm font-semibold"><QrCode className="h-4 w-4" aria-hidden="true" />Pix Automático — Sicredi</h4>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">O cliente precisará autorizar a cobrança no aplicativo do banco.</p>
      </div>
      <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-slate-500">Dia de vencimento</dt><dd>{billingDay ? `Dia ${billingDay}` : 'Não informado'}</dd></div>
        <div><dt className="text-xs text-slate-500">Responsável financeiro</dt><dd>{nome.trim() || 'Este cliente'}</dd></div>
        <div><dt className="text-xs text-slate-500">Situação atual</dt>
          <dd>{rec ? ROTULO_AUTORIZACAO[rec.status] ?? rec.status : clientId && canFinance && !contexto ? 'Consultando…' : 'Autorização ainda não solicitada'}</dd></div>
      </dl>
      {ativa ? (
        <p className="text-sm">Este cliente já tem uma autorização ({ROTULO_AUTORIZACAO[rec!.status] ?? rec!.status}, início em {formatDate(rec!.data_inicial)}). Salvar o cadastro não cria outra.</p>
      ) : canFinance && (
        <>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={solicitar}
            onChange={(e) => onOpcoesChange({ ...opcoes, solicitar: e.target.checked })} />
          {rec ? 'Solicitar uma nova autorização ao Sicredi ao salvar' : 'Solicitar a autorização ao Sicredi ao salvar'}
        </label>
        {solicitar && <FormGrid cols={2}>
          <FormField label="Início sugerido da autorização"
            hint="Autorizar não cria cobrança nesta data. Cada débito segue o vencimento gerado no fechamento.">
            <Input type="date" min={minimo} value={valorData}
              onChange={(e) => onOpcoesChange({ ...opcoes, data_inicial: e.target.value })} />
          </FormField>
          <FormField label="Envio da autorização ao cliente">
            <Select value={opcoes.enviar_convite && !semEmail ? 'email' : 'manual'}
              onChange={(e) => onOpcoesChange({ ...opcoes, enviar_convite: e.target.value === 'email' })}>
              <option value="email" disabled={semEmail}>{semEmail ? 'E-mail (cadastre um e-mail)' : `E-mail para ${email.trim()}`}</option>
              <option value="manual">Não enviar agora (compartilhar o QR manualmente)</option>
            </Select>
          </FormField>
        </FormGrid>}
        </>
      )}
      {!ativa && solicitar && valorData && valorData < minimo && (
        <p role="alert" className="text-xs text-rose-600">O início precisa ter ao menos dois dias de antecedência ({formatDate(minimo)} ou depois).</p>
      )}
      {!canFinance && (
        <p className="rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          A autorização no banco é gerada por um usuário do Financeiro. Depois de salvar, o cliente aparece como pendente no painel Pix Automático do Financeiro.
        </p>
      )}
      {canFinance && conexao && !conexao.escrita_habilitada && !ativa && (
        <p className="rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          A integração Pix Automático não está liberada neste ambiente. O cadastro será salvo, mas a autorização automática não será iniciada.
        </p>
      )}
      {canFinance && conexao?.smtp_configurado === false && opcoes.enviar_convite && !ativa && solicitar && (
        <p className="text-xs text-amber-800 dark:text-amber-200">O e-mail do sistema não está configurado: o convite será registrado como não enviado e o QR ficará disponível para envio manual.</p>
      )}
      {contexto?.erro && <p className="text-xs text-amber-800 dark:text-amber-200">{contexto.erro}</p>}
    </section>
  );
}
