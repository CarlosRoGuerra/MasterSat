import { apiFetch } from '@/lib/api';

export type AcaoLoteBoleto = 'emitir' | 'email' | 'email_com_nfse' | 'nfse_email';
export type ResultadoLoteBoletos = {
  processados: number[];
  falhas: { id: number; mensagem: string }[];
};

/** Usa o estado bancário da carteira para não repetir o registro de boletos. */
export function separarBoletosPorRegistro(
  ids: number[],
  billings: ReadonlyArray<{ id: number; boleto_ailos?: boolean }>,
): { existentes: number[]; ausentes: number[] } {
  const registrados = new Set(billings.filter(b => b.boleto_ailos).map(b => b.id));
  const selecionados = [...new Set(ids)];
  return {
    existentes: selecionados.filter(id => registrados.has(id)),
    ausentes: selecionados.filter(id => !registrados.has(id)),
  };
}

/** Processa somente os IDs selecionados, mantendo o resultado de cada cobrança. */
export async function processarBoletosSelecionados(
  ids: number[],
  acao: AcaoLoteBoleto,
  token: string,
  onProgress?: (concluidos: number, total: number) => void,
): Promise<ResultadoLoteBoletos> {
  const selecionados = [...new Set(ids)];
  const resultado: ResultadoLoteBoletos = { processados: [], falhas: [] };

  for (const [indice, id] of selecionados.entries()) {
    try {
      if (acao === 'emitir') {
        const boleto = await apiFetch<{ linha_digitavel?: string | null; codigo_barras?: string | null }>('/ailos/boletos', {
          method: 'POST', body: JSON.stringify({ billing_id: id }),
        }, token);
        if (!boleto.linha_digitavel || !boleto.codigo_barras) {
          throw new Error('A Ailos ainda não confirmou linha digitável e código de barras.');
        }
      } else if (acao === 'email_com_nfse') {
        await apiFetch(`/boletos/${id}/enviar-email?incluir_nfse=true`, { method: 'POST' }, token);
      } else if (acao === 'nfse_email') {
        await apiFetch(`/nfse/${id}/enviar-email`, { method: 'POST' }, token);
      } else {
        await apiFetch(`/boletos/${id}/enviar-email`, { method: 'POST' }, token);
      }
      resultado.processados.push(id);
    } catch (error) {
      resultado.falhas.push({
        id,
        mensagem: error instanceof Error ? error.message : 'Erro inesperado.',
      });
    }
    onProgress?.(indice + 1, selecionados.length);
  }

  return resultado;
}
