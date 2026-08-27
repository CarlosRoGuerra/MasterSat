import type { BillingItem, ClientFormState, ContactItem, ContractSheetItem } from './types';

export function formatCurrency(value: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0);
}

/** Valor atualizado com juros de atraso, quando o backend já o calculou. */
export function valorComJuros(b: BillingItem): number | null {
  return b.valor_com_juros ?? null;
}

export function orderTypeLabel(type: string) {
  return ({ instalacao: 'Instalação', manutencao: 'Manutenção', retirada: 'Retirada', visita_tecnica: 'Visita técnica' } as Record<string, string>)[type] ?? type;
}

export const emptyContact: ContactItem = { name: '', phone: '', email: '', role: '' };

export const initialForm: ClientFormState = {
  name: '',
  cpf_cnpj: '',
  type: 'pf',
  status: 'ativo',
  email: '',
  extra_emails: '',
  phone: '',
  contacts: [],
  zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
  notes: '',
  billing_day: '',
  rg_ie: '',
  birth_date: '',
  em1_name: '',
  em1_phone: '',
  em1_mobile: '',
  em2_name: '',
  em2_phone: '',
  em2_mobile: '',
  boleto_format: 'unico',
  boleto_fee: 'nao',
  issue_invoice: 'sim',
  tributacao: 'dentro_municipio',
  iss_retido: 'nao',
  optante_simples: 'sim',
  delivery_method: 'email',
  send_boleto_whatsapp: false,
  trade_name: '',
};

export const documentCategoryOptions = ['cnh', 'rg', 'cpf', 'contrato', 'comprovante_endereco', 'cartao_cnpj', 'contrato_social', 'outro'];

/**
 * Situação do contrato. Só entra "Em vigor" depois de assinado (o contrato
 * assinado é enviado no bloco abaixo); antes disso fica "Aguardando assinatura".
 */
export function contractSituacao(c: ContractSheetItem): { label: string; variant: 'success' | 'warning' | 'danger' } {
  if (c.status === 'cancelado' || c.status === 'encerrado') return { label: 'Cancelado', variant: 'warning' };
  if (!c.signed) return { label: 'Aguardando assinatura', variant: 'warning' };
  const hoje = new Date().toISOString().slice(0, 10);
  if (c.end_date && c.end_date < hoje) return { label: 'Vencido', variant: 'danger' };
  return { label: 'Em vigor', variant: 'success' };
}

/** "enviado por Fulano em 12/08/2026" — metadados do anexo, quando houver. */
export function envioMeta(doc: { uploaded_by?: string | null; created_at?: string | null }): string | null {
  if (!doc.uploaded_by && !doc.created_at) return null;
  const quem = doc.uploaded_by ? `enviado por ${doc.uploaded_by}` : 'enviado';
  const quando = doc.created_at ? ` em ${new Date(doc.created_at).toLocaleDateString('pt-BR')}` : '';
  return `${quem}${quando}`;
}

export const fileInputClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white file:mr-4 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white dark:file:bg-brand-500';

export function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

export function parseExtraEmails(value: string) {
  return value.split(/[,;\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
}

// Botão de ação colorido — badges quadrados no código semântico do sistema de referência
export const COLOR_CLASSES: Record<string, string> = {
  purple: 'bg-[#7952B3] text-white hover:brightness-110',   // veículos do cliente
  yellow: 'bg-[#FFC107] text-white hover:brightness-105',   // veículos como interveniente
  green:  'bg-[#28A745] text-white hover:brightness-110',   // central financeira / boletos
  white:  'border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300', // NF / documentação
  teal:   'bg-[#20C997] text-white hover:brightness-110',   // imprimir ficha de adesão
  blue:   'bg-[#17A2B8] text-white hover:brightness-110',   // editar
  red:    'bg-[#DC3545] text-white hover:brightness-110',   // excluir
  slate:  'bg-slate-500 text-white hover:bg-slate-600',
};
