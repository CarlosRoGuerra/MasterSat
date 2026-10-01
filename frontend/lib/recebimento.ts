// Recebimento com diferença de valor (Fase 03, FIN-06).
//
// O backend recusa (409 recebimento_divergente) um recebimento cujo valor
// difere do título sem que o operador diga o que é a diferença. Aqui fica a
// mesma conta, em centavos, para a tela mostrar as opções antes de enviar.

export type TratamentoDiferenca = 'desconto' | 'parcial' | 'encargos' | 'credito';

export const ROTULO_TRATAMENTO: Record<TratamentoDiferenca, string> = {
  desconto: 'Desconto concedido (quita com abatimento)',
  parcial: 'Pagamento parcial (o saldo vira nova cobrança)',
  encargos: 'Multa/juros de atraso',
  credito: 'Crédito a favor do cliente',
};

export type Diferenca = {
  centavos: number; // recebido − título, em centavos
  tipo: 'igual' | 'menor' | 'maior';
  tratamentos: TratamentoDiferenca[];
};

// Mesma convenção do resto da tela: vírgula OU ponto como separador decimal
// (o valor vem pré-preenchido como "99.9"). Sem separador de milhar.
export function paraCentavos(valor: string | number): number | null {
  const texto = typeof valor === 'number' ? String(valor) : valor.trim().replace(',', '.');
  if (texto === '') return null;
  const numero = Number(texto);
  if (!Number.isFinite(numero)) return null;
  return Math.round(numero * 100);
}

export function diferencaRecebimento(valorTitulo: number, valorRecebido: string | number): Diferenca | null {
  const recebido = paraCentavos(valorRecebido);
  const titulo = paraCentavos(valorTitulo);
  if (recebido === null || titulo === null) return null;
  const centavos = recebido - titulo;
  if (centavos === 0) return { centavos, tipo: 'igual', tratamentos: [] };
  return centavos < 0
    ? { centavos, tipo: 'menor', tratamentos: ['desconto', 'parcial'] }
    : { centavos, tipo: 'maior', tratamentos: ['encargos', 'credito'] };
}

export type FormRecebimento = {
  paid_amount: string;
  tratamento: TratamentoDiferenca | '';
  justificativa: string;
  saldo_vencimento: string;
};

/** Mensagem do que falta preencher, ou null se pode enviar. Mesmas regras do backend. */
export function pendenciaDoFormulario(valorTitulo: number, form: FormRecebimento): string | null {
  const recebido = paraCentavos(form.paid_amount);
  if (recebido === null || recebido <= 0) return 'Informe um valor recebido maior que zero.';
  const dif = diferencaRecebimento(valorTitulo, form.paid_amount);
  if (!dif || dif.tipo === 'igual') return null;
  if (!form.tratamento || !dif.tratamentos.includes(form.tratamento)) {
    return 'O valor recebido difere do título: escolha o que é a diferença.';
  }
  const justificativaObrigatoria = form.tratamento !== 'encargos';
  if (justificativaObrigatoria && form.justificativa.trim().length < 3) return 'Justifique a diferença.';
  if (form.tratamento === 'parcial' && !form.saldo_vencimento) return 'Informe o vencimento da cobrança do saldo.';
  return null;
}

/** Campos extras do POST /billings/{id}/receive (vazio quando o valor é igual). */
export function camposDiferenca(valorTitulo: number, form: FormRecebimento): Record<string, string> {
  const dif = diferencaRecebimento(valorTitulo, form.paid_amount);
  if (!dif || dif.tipo === 'igual' || !form.tratamento) return {};
  return {
    tratamento_diferenca: form.tratamento,
    ...(form.justificativa.trim() ? { justificativa_diferenca: form.justificativa.trim() } : {}),
    ...(form.tratamento === 'parcial' ? { saldo_vencimento: form.saldo_vencimento } : {}),
  };
}
