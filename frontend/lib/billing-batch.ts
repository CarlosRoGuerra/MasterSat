export type BillingBatchAction = 'cancelar' | 'excluir';

export type BillingBatchResult = {
  simulacao: boolean;
  action: BillingBatchAction;
  total_enviados: number;
  aplicados: number;
  ignorados: number;
  revisao: string;
  itens: {
    billing_id: number;
    title?: string | null;
    status?: string | null;
    situacao: 'aplicavel' | 'aplicado' | 'ignorado';
    motivo?: string | null;
    nosso_numero?: string | null;
    baixa_bancaria_pendente: boolean;
    libera_competencia: boolean;
  }[];
};

export const canSelectBilling = (billing: { status: string }) =>
  ['pendente', 'vencida', 'cancelada'].includes(billing.status);

export function toggleVisibleBillings(selected: number[], visible: { id: number; status: string }[], checked: boolean) {
  const ids = visible.filter(canSelectBilling).map((billing) => billing.id);
  return checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id));
}

export function billingBatchSummary(result: BillingBatchResult) {
  return `${result.aplicados} boleto(s) ${result.action === 'cancelar' ? 'cancelado(s)' : 'excluído(s)'}`
    + (result.ignorados ? ` · ${result.ignorados} ignorado(s). Confira os motivos no resultado.` : '.');
}
