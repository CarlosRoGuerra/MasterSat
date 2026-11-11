// Geração de carnê a partir do plano (/billings/parcelar).
//
// Quando os meses estão presos só por cobranças canceladas — inclusive as
// excluídas antes de o cancelamento passar a liberar o mês — o backend
// responde 409 `competencias_canceladas` com a explicação (e o aviso de baixa
// no site do Sicredi/Ailos). O operador confirma uma vez e a chamada é
// repetida liberando esses meses; recusar não gera nada.

type ErroApi = Error & { status?: number; detail?: { code?: string } };

export async function parcelarLiberandoCanceladas<T>(
  parcelar: (liberarCanceladas: boolean) => Promise<T>,
  confirmar: (mensagem: string) => boolean,
): Promise<T | null> {
  try {
    return await parcelar(false);
  } catch (err) {
    const { status, detail } = err as ErroApi;
    if (status !== 409 || detail?.code !== 'competencias_canceladas') throw err;
    if (!confirmar((err as Error).message)) return null;
    return parcelar(true);
  }
}
