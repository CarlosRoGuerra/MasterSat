// Cancelamento de cobrança com as confirmações que o backend pode exigir.
//
// POST /billings/{id}/cancel responde 409 quando falta uma confirmação:
//   - boleto_ailos_registrado → o título continua ativo no banco;
//   - titulo_substituto      → a cobrança é boleto único/negociação e as
//                               originais serão reabertas (Fase 02, FIN-01);
//   - baixa_bancaria_pendente → pediu para liberar o mês, mas o boleto ainda
//                               é pagável (Fase 03, FIN-02): oferece cancelar
//                               SEM liberar (libera depois da baixa).
// Cada uma é perguntada ao operador uma única vez; recusar encerra sem
// cancelar. Qualquer outro erro sobe para quem chamou.

export type FlagsCancelamento = {
  confirmar_boleto_ailos: boolean;
  reverter_substituicao: boolean;
  // true = o operador aceitou cancelar sem liberar a competência agora.
  desistir_liberacao: boolean;
};

type ErroApi = Error & { status?: number; detail?: { code?: string; message?: string } };

const CONFIRMACOES: Record<string, { flag: keyof FlagsCancelamento; sufixo: string }> = {
  boleto_ailos_registrado: { flag: 'confirmar_boleto_ailos', sufixo: '' },
  titulo_substituto: { flag: 'reverter_substituicao', sufixo: '\n\nOK = cancelar e reabrir as originais.' },
  baixa_bancaria_pendente: {
    flag: 'desistir_liberacao',
    sufixo: '\n\nOK = cancelar SEM liberar o mês agora (libere depois que a baixa for confirmada).',
  },
};

export async function cancelarComConfirmacoes(
  chamar: (flags: FlagsCancelamento) => Promise<unknown>,
  confirmar: (mensagem: string) => boolean,
): Promise<{ cancelada: boolean; flags: FlagsCancelamento }> {
  const flags: FlagsCancelamento = {
    confirmar_boleto_ailos: false, reverter_substituicao: false, desistir_liberacao: false,
  };
  for (;;) {
    try {
      await chamar({ ...flags });
      return { cancelada: true, flags };
    } catch (err) {
      const e = err as ErroApi;
      const pedido = e.status === 409 && e.detail?.code ? CONFIRMACOES[e.detail.code] : undefined;
      if (!pedido || flags[pedido.flag]) throw err;
      if (!confirmar(`${e.detail?.message ?? ''}${pedido.sufixo}`)) return { cancelada: false, flags };
      flags[pedido.flag] = true;
    }
  }
}

/** Corpo do POST /cancel a partir das flags (a de liberação não vai ao backend). */
export function corpoCancelamento(
  reason: string, liberarCompetencia: boolean, flags: FlagsCancelamento,
): Record<string, unknown> {
  return {
    reason,
    liberar_competencia: liberarCompetencia && !flags.desistir_liberacao,
    confirmar_boleto_ailos: flags.confirmar_boleto_ailos,
    reverter_substituicao: flags.reverter_substituicao,
  };
}
