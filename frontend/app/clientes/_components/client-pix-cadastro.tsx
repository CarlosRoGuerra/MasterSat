'use client';

import { apiFetch } from '@/lib/api';
import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { ClientPixAutomatico, ROTULO_AUTORIZACAO } from './client-pix-automatico';
import type { PixCadastroContexto, PixCadastroOpcoes } from './client-form-pix-block';
import type { Client } from './types';

export type PixResultadoCadastro = {
  tipo: 'gerada' | 'pendente' | 'existente';
  titulo: string;
  mensagem: string;
};

type Recorrencia = { status: string; ativa: boolean; pix_copia_cola: string | null;
  convite_status?: string | null; convite_destino?: string | null; convite_erro?: string | null };

const PENDENTE = 'Cadastro salvo — autorização Pix pendente.';
const GERADA = 'Cadastro salvo — autorização Pix gerada.';

function textoConvite(rec: Recorrencia): string {
  switch (rec.convite_status) {
    case 'enviado': return ` Convite enviado por e-mail para ${rec.convite_destino}.`;
    case 'erro': return ` O convite por e-mail não foi enviado: ${rec.convite_erro ?? 'erro desconhecido'} Compartilhe o QR abaixo manualmente.`;
    case 'desconhecido': return ' O envio do convite por e-mail ficou sem confirmação; confirme com o cliente antes de reenviar.';
    case 'pendente': return ' O convite por e-mail será enviado automaticamente assim que possível.';
    default: return ' Compartilhe o QR Code ou o código Copia e Cola abaixo com o cliente.';
  }
}

/**
 * Continuação do cadastro: chamada SOMENTE depois que o cliente foi salvo.
 * Nunca lança erro (o cadastro já está salvo) e nunca repete a criação: a
 * autorização ativa existente é reaproveitada pelo próprio servidor.
 */
export async function iniciarAutorizacaoPix({ clientId, token, podeGerar, contexto, opcoes }: {
  clientId: number; token: string; podeGerar: boolean;
  contexto: PixCadastroContexto | null; opcoes: PixCadastroOpcoes;
}): Promise<PixResultadoCadastro> {
  const atual = contexto?.recorrencia;
  if (atual?.ativa) {
    return { tipo: 'existente', titulo: 'Cadastro salvo.',
      mensagem: `Autorização Pix Automático mantida (${ROTULO_AUTORIZACAO[atual.status] ?? atual.status}).` };
  }
  if (!podeGerar) {
    return { tipo: 'pendente', titulo: PENDENTE, mensagem: 'A autorização no banco é gerada por um usuário do Financeiro. '
      + 'O cliente aparece como "Autorização não gerada" no painel Pix Automático do Financeiro.' };
  }
  if (contexto?.conexao && !contexto.conexao.escrita_habilitada) {
    return { tipo: 'pendente', titulo: PENDENTE, mensagem: 'A integração Pix Automático não está liberada neste ambiente, '
      + 'por isso a autorização automática não foi iniciada. O cliente ficou pendente no painel Pix Automático do Financeiro.' };
  }
  try {
    const data = await apiFetch<{ recorrencia: Recorrencia | null }>(`/sicredi-pix/clientes/${clientId}/autorizacao`, {
      method: 'POST', body: JSON.stringify({ data_inicial: opcoes.data_inicial, enviar_convite: opcoes.enviar_convite }),
    }, token);
    const rec = data.recorrencia;
    if (rec?.status === 'CRIADA' && rec.pix_copia_cola) {
      return { tipo: 'gerada', titulo: GERADA, mensagem: 'O Sicredi devolveu o QR Code e o código Copia e Cola. '
        + 'Isso ainda não é uma autorização: o cliente precisa aprovar no aplicativo do banco.' + textoConvite(rec) };
    }
    if (rec?.status === 'APROVADA') {
      return { tipo: 'existente', titulo: 'Cadastro salvo.', mensagem: 'O cliente já autorizou o Pix Automático.' };
    }
    return { tipo: 'pendente', titulo: PENDENTE, mensagem: rec?.status === 'CRIADA'
      ? 'A autorização foi criada, mas o Sicredi ainda não devolveu o QR Code. O sistema consulta automaticamente.' + textoConvite(rec)
      : 'O Sicredi ainda não confirmou o resultado. O sistema consulta automaticamente e não cria outra autorização sem essa confirmação.' };
  } catch (err) {
    const motivo = err instanceof Error ? err.message : 'falha de comunicação.';
    return { tipo: 'pendente', titulo: PENDENTE, mensagem: `A autorização não foi concluída agora: ${motivo} `
      + 'O cadastro continua salvo. Confira a situação abaixo; o sistema não repete a criação sem consultar o banco.' };
  }
}

export function ClientPixResultadoModal({ resultado, client, token, canFinance, onClose }: {
  resultado: PixResultadoCadastro | null;
  client: Pick<Client, 'id' | 'name' | 'billing_day' | 'phone'> | null;
  token: string | null;
  canFinance: boolean;
  onClose: () => void;
}) {
  if (!resultado || !client) return null;
  return (
    <Modal open onClose={onClose} title={resultado.titulo} size="xl"
      footer={<Button type="button" onClick={onClose}>Concluir</Button>}>
      <div className="space-y-4">
        <p role="status" className={resultado.tipo === 'gerada'
          ? 'rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200'
          : 'rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'}>
          {resultado.mensagem}
        </p>
        {canFinance && token && <ClientPixAutomatico key={client.id} client={client} token={token} />}
      </div>
    </Modal>
  );
}
