// Exclusão de cobrança CANCELADA pela tela de detalhes do Financeiro.
//
// DELETE /billings/{id} é exclusão lógica (o registro e o histórico ficam no
// banco). O backend continua recusando — e a tela não tenta contornar:
//   - cobrança paga;
//   - qualquer título no banco (registrado, baixado, em registro, desfecho
//     desconhecido, remessa CNAB): sairia da conciliação (FIN-02);
//   - original de boleto único/negociação que ainda vale (o mês seria
//     cobrado de novo por cima da negociação).
// Boleto único cancelado que ainda aponta para originais responde 409
// `titulo_substituto`: a exclusão só segue se o operador aceitar reabrir as
// originais (mesma confirmação do cancelamento).
//
// Mensalidade cancelada OCUPA o mês do contrato — é o que impede o fechamento
// de recobrar um mês dispensado ou negociado. Excluir libera o mês; a
// confirmação diz isso com todas as letras.

import type { TituloBancario } from '@/lib/titulo-bancario';

export const TIPOS_QUE_OCUPAM_MES = ['recorrente', 'prorata', 'primeira_mensalidade', 'carne'];

export type CobrancaParaExcluir = {
  id: number;
  status: string;
  billing_type: string;
  contract_id?: number | null;
  competencia_liberada?: boolean;
  period_label?: string | null;
  titulo_bancario?: Pick<TituloBancario, 'estado'> | null;
};

type ErroApi = Error & { status?: number; detail?: { code?: string; message?: string } };

/** Por que a exclusão não é oferecida (null = pode tentar). */
export function motivoBloqueioExclusao(cobranca: CobrancaParaExcluir): string | null {
  if (cobranca.status !== 'cancelada') return 'Só cobrança cancelada pode ser excluída.';
  const estado = cobranca.titulo_bancario?.estado;
  if (estado && estado !== 'sem_titulo') {
    return 'Cobrança com boleto no banco não é excluída: o histórico bancário e a conciliação dependem dela.';
  }
  return null;
}

export function ocupaCompetencia(cobranca: CobrancaParaExcluir): boolean {
  return TIPOS_QUE_OCUPAM_MES.includes(cobranca.billing_type)
    && !!cobranca.contract_id
    && !cobranca.competencia_liberada;
}

export function mensagemConfirmacaoExclusao(cobranca: CobrancaParaExcluir): string {
  const base = `Excluir a cobrança cancelada #${cobranca.id}? Ela sai da carteira; o histórico continua registrado.`;
  if (!ocupaCompetencia(cobranca)) return base;
  return `${base}\n\nAtenção: esta mensalidade ainda ocupa a competência ${cobranca.period_label ?? 'do contrato'}. `
    + 'Excluindo, o mês fica livre e o fechamento pode gerar a cobrança de novo. '
    + 'Se o mês foi dispensado ou negociado, mantenha a cobrança cancelada.';
}

/** Chama o DELETE; se o backend pedir a reversão da substituição, pergunta
 *  uma vez e repete. Recusar encerra sem excluir. */
export async function excluirComConfirmacoes(
  chamar: (reverterSubstituicao: boolean) => Promise<{ reabertas?: number[] } | unknown>,
  confirmar: (mensagem: string) => boolean,
): Promise<{ excluida: boolean; reabertas: number[] }> {
  let reverter = false;
  for (;;) {
    try {
      const resposta = (await chamar(reverter)) as { reabertas?: number[] } | undefined;
      return { excluida: true, reabertas: resposta?.reabertas ?? [] };
    } catch (err) {
      const e = err as ErroApi;
      if (reverter || e.status !== 409 || e.detail?.code !== 'titulo_substituto') throw err;
      if (!confirmar(`${e.detail?.message ?? ''}\n\nOK = excluir e reabrir as originais.`)) {
        return { excluida: false, reabertas: [] };
      }
      reverter = true;
    }
  }
}
