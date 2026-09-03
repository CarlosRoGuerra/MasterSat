/**
 * Tipos de domínio da tela de Clientes, compartilhados entre o container
 * (app/clientes/page.tsx) e os modais extraídos para _components/.
 *
 * Não vêm do OpenAPI (lib/domain-types.ts): são recortes específicos desta
 * tela, com nomes/campos que nem sempre batem 1:1 com os schemas do backend
 * (ex.: BillingItem usa `valor_com_juros`, calculado no cliente).
 */
import type { ClientStatus, DocumentReviewStatus as ReviewStatus } from '@/lib/domain-types';

export type ClientType = 'pf' | 'pj';

export type ContactItem = { name: string; phone: string; email: string; role: string };

export type Client = {
  id: number;
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
  email?: string | null;
  extra_emails?: string[] | null;
  phone?: string | null;
  contacts?: ContactItem[] | null;
  zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
  notes?: string | null;
  billing_day?: number | null;
  rg_ie?: string | null;
  birth_date?: string | null;
  emergency_contacts?: { name?: string | null; phone?: string | null; mobile?: string | null }[] | null;
  boleto_format?: string | null;
  boleto_fee?: string | null;
  issue_invoice?: string | null;
  tributacao?: string | null;
  iss_retido?: string | null;
  optante_simples?: string | null;
  delivery_method?: string | null;
  send_boleto_whatsapp?: boolean | null;
  trade_name?: string | null;
  /** Há um contrato assinado (categoria 'contrato') guardado nos documentos? */
  contrato_armazenado?: boolean | null;
};

export type ClientDocument = {
  id: number;
  file_name: string;
  category: string;
  review_status: ReviewStatus;
  review_notes?: string | null;
  url: string;
  download_url: string;
  created_at?: string | null;
  uploaded_by?: string | null;
};

export type VehicleSummary = { id: number; client_id: number; plate: string; status: string };

export type VehicleDetailed = {
  id: number;
  plate: string;
  type?: string | null;
  brand?: string | null;
  model?: string | null;
  status: string;
  // campos de rastreador enriquecidos após join
  tracker_imei?: string | null;
  tracker_brand?: string | null;
  tracker_model?: string | null;
  tracker_plan?: string | null;
};

export type BillingItem = {
  id: number;
  title?: string | null;
  billing_type: string;
  due_date: string;
  amount: number;
  valor_com_juros?: number | null;
  paid_amount?: number | null;
  status: string;
  period_label?: string | null;
  installment_number?: number | null;
  installment_total?: number | null;
  payment_date?: string | null;
  created_at?: string | null;
  vehicle_plate?: string | null;
  /** Há boleto registrado na Ailos? Sem isso não existe PDF para baixar. */
  boleto_ailos?: boolean;
};

export type CarneParcelaDetalhe = {
  billing_id: number;
  numero_parcela: number | null;
  vencimento: string | null;
  valor: number;
  status: string;
  data_pagamento: string | null;
};

export type CarneItem = {
  lote_id: number;
  ticket?: string | null;
  criado_em?: string | null;
  parcelas: number;
  parcelas_registradas: number;
  parcelas_pagas: number;
  total: number;
  valor_pago: number;
  status: string;
  parcelas_detalhe: CarneParcelaDetalhe[];
};

export type IntervContract = { id: number; client_name?: string | null; vehicle_plate?: string | null; plan_name?: string | null; status: string; monthly_value?: number | null };

export type BillingChange = {
  id: number;
  field_name: string;
  previous_value?: string | null;
  new_value?: string | null;
  justification: string;
  created_at?: string | null;
};

export type NfseItem = {
  billing_id: number;
  status: string;
  numero_nfse?: string | null;
  codigo_verificacao?: string | null;
  link_visualizacao?: string | null;
  data_emissao?: string | null;
  erro_mensagem?: string | null;
  valor?: number | null;
  titulo?: string | null;
};

export type ClientFormState = {
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
  email: string;
  extra_emails: string;
  phone: string;
  contacts: ContactItem[];
  zip_code: string;
  address_line: string;
  address_number: string;
  address_complement: string;
  neighborhood: string;
  city: string;
  state: string;
  notes: string;
  billing_day: string;
  rg_ie: string;
  birth_date: string;
  em1_name: string;
  em1_phone: string;
  em1_mobile: string;
  em2_name: string;
  em2_phone: string;
  em2_mobile: string;
  boleto_format: string;
  boleto_fee: string;
  issue_invoice: string;
  tributacao: string;
  iss_retido: string;
  optante_simples: string;
  delivery_method: string;
  send_boleto_whatsapp: boolean;
  trade_name: string;
};

/** Registro de contrato do cliente (para listar/excluir na modal de contratos). */
export type ContractSheetItem = {
  id: number;
  plan_name?: string | null;
  vehicle_plate?: string | null;
  tracker_identifier?: string | null;
  start_date: string;
  end_date?: string | null;
  status: string;
  signed?: boolean | null;
  monthly_value?: number | null;
};

export type ClientSortField = 'id' | 'name' | 'trade_name' | 'cpf_cnpj' | 'status';
export type ClientSort = { field: ClientSortField; dir: 'asc' | 'desc' };
