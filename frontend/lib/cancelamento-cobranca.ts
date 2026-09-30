// Cancelamento de cobrança com as confirmações que o backend pode exigir.
//
// POST /billings/{id}/cancel responde 409 quando falta uma confirmação:
//   - boleto_ailos_registrado → o título continua ativo no banco;
//   - titulo_substituto      → a cobrança é boleto único/negociação e as
//                               originais serão reabertas (Fase 02, FIN-01).
// Cada uma é perguntada ao operador uma única vez; recusar encerra sem
// cancelar. Qualquer outro erro sobe para quem chamou.

export type FlagsCancelamento = {
  confirmar_boleto_ailos: boolean;
  reverter_substituicao: boolean;
};

type ErroApi = Error & { status?: number; detail?: { code?: string; message?: string } };

const CONFIRMACOES: Record<string, { flag: keyof FlagsCancelamento; sufixo: string }> = {
  boleto_ailos_registrado: { flag: 'confirmar_boleto_ailos', sufixo: '' },
  titulo_substituto: { flag: 'reverter_substituicao', sufixo: '\n\nOK = cancelar e reabrir as originais.' },
};

export async function cancelarComConfirmacoes(
  chamar: (flags: FlagsCancelamento) => Promise<unknown>,
  confirmar: (mensagem: string) => boolean,
): Promise<{ cancelada: boolean; flags: FlagsCancelamento }> {
  const flags: FlagsCancelamento = { confirmar_boleto_ailos: false, reverter_substituicao: false };
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
