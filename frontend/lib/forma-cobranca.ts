/** Forma de cobrança do cliente (cadastro). Filtra o relatório de cobranças e
 *  a simulação do fechamento. Vazio = não informado. */
export const FORMAS_COBRANCA = [
  { value: 'boleto_mensal', label: 'Boleto mensal' },
  { value: 'carne_ailos', label: 'Carnê Ailos' },
  { value: 'carne_simples', label: 'Carnê simples' },
  { value: 'cartao_credito', label: 'Cartão de crédito' },
] as const;

export type FormaCobranca = (typeof FORMAS_COBRANCA)[number]['value'];

/** Opções de FILTRO: além das formas, quem ainda está sem forma informada. */
export const FILTROS_FORMA_COBRANCA = [
  ...FORMAS_COBRANCA,
  { value: 'nao_informado', label: 'Não informada' },
] as const;

export function rotuloFormaCobranca(value: string | null | undefined): string {
  return FORMAS_COBRANCA.find((f) => f.value === value)?.label ?? 'Não informada';
}
