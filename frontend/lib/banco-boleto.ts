export type BancoBoleto = 'ailos' | 'sicredi';

export const nomeBancoBoleto = (banco: BancoBoleto = 'ailos') => banco === 'sicredi' ? 'Sicredi' : 'Ailos';

export const chaveCarne = (loteId: number, banco: BancoBoleto = 'ailos') => `${banco}:${loteId}`;

/** Um título registrado fica no seu banco; mudar a escolha não pode reemiti-lo. */
export function cobrancaCompativelComBanco(
  cobranca: { boleto_ailos?: boolean; payment_method?: string | null; titulo_bancario?: { canal?: string | null } | null },
  banco: BancoBoleto,
) {
  if (cobranca.payment_method === 'pix_automatico' || cobranca.titulo_bancario?.canal === 'sicredi_pix') return false;
  if (!cobranca.boleto_ailos) return true;
  return (cobranca.titulo_bancario?.canal === 'sicredi_api') === (banco === 'sicredi');
}

export const caminhoPdfCarne = (loteId: number, banco: BancoBoleto = 'ailos', parcial = false) => {
  const params = new URLSearchParams();
  if (banco === 'sicredi') params.set('banco', banco);
  if (parcial) params.set('parcial', 'true');
  return `/boletos/carne/${loteId}/pdf${params.size ? `?${params}` : ''}`;
};
