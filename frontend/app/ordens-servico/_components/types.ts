import type {
  OrderType,
  OrderStatus,
  OrderPriority,
  DocumentReviewStatus as ReviewStatus,
} from '@/lib/domain-types';

export type ChecklistItem = { description: string; done: boolean; notes?: string | null };

export type ServiceOrder = {
  id: number;
  number: string;
  type: OrderType;
  status: OrderStatus;
  priority: OrderPriority;
  client_id: number;
  vehicle_id?: number | null;
  tracker_id?: number | null;
  technician_id?: number | null;
  scheduled_at?: string | null;
  executed_at?: string | null;
  checklist?: ChecklistItem[] | null;
  observations?: string | null;
  problem_description?: string | null;
  execution_description?: string | null;
  technician_signed_at?: string | null;
  client_signed_at?: string | null;
  client_name?: string | null;
  vehicle_plate?: string | null;
  tracker_label?: string | null;
  technician_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type OrderLog = {
  id: number;
  previous_status?: OrderStatus | null;
  new_status: OrderStatus;
  notes?: string | null;
  changed_by_id?: number | null;
  changed_by_name?: string | null;
  created_at?: string | null;
};

export type OrderDocument = {
  id: number;
  file_name: string;
  category: string;
  content_type?: string;
  review_status: ReviewStatus;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

export type ServiceOrderMaterial = {
  id: number;
  service_order_id: number;
  service_product_id?: number | null;
  service_product_name?: string | null;
  description: string;
  quantity: string;
  unit?: string | null;
  unit_price?: string | null;
  notes?: string | null;
};

export type DetailsTab = 'detalhes' | 'checklist' | 'materiais' | 'fotos' | 'assinaturas' | 'documentos' | 'historico';

export const TAB_LABEL: Record<DetailsTab, string> = {
  detalhes: 'Detalhes',
  checklist: 'Checklist',
  materiais: 'Materiais',
  fotos: 'Fotos',
  assinaturas: 'Assinaturas',
  documentos: 'Documentos',
  historico: 'Histórico',
};

export const orderTypeOptions: { value: OrderType; label: string }[] = [
  { value: 'instalacao', label: 'Instalação' },
  { value: 'manutencao', label: 'Manutenção / troca' },
  { value: 'retirada', label: 'Retirada' },
  { value: 'visita_tecnica', label: 'Visita técnica / suporte' },
];

export const statusOptions: { value: OrderStatus; label: string }[] = [
  { value: 'aberta', label: 'Aberta' },
  { value: 'em_andamento', label: 'Em andamento' },
  { value: 'concluida', label: 'Concluída' },
  { value: 'cancelada', label: 'Cancelada' },
];

export const priorityOptions: { value: OrderPriority; label: string }[] = [
  { value: 'baixa', label: 'Baixa' },
  { value: 'normal', label: 'Normal' },
  { value: 'alta', label: 'Alta' },
  { value: 'urgente', label: 'Urgente' },
];

export const checklistTemplates: Record<OrderType, string[]> = {
  instalacao: ['Confirmar cliente e veículo', 'Conferir posição e local do equipamento', 'Testar comunicação', 'Registrar fotos da instalação'],
  manutencao: ['Validar causa da manutenção', 'Conferir chicote e alimentação', 'Executar teste funcional', 'Registrar evidência pós-serviço'],
  retirada: ['Confirmar autorização de retirada', 'Retirar equipamento com segurança', 'Registrar estado do equipamento', 'Atualizar devolução ao estoque'],
  visita_tecnica: ['Validar solicitação', 'Executar atendimento em campo', 'Registrar evidências', 'Formalizar conclusão'],
};

export const pdfKinds = [
  { value: 'ordem_servico', label: 'Ordem de Serviço' },
  { value: 'termo_instalacao', label: 'Termo de instalação' },
  { value: 'termo_retirada', label: 'Termo de retirada' },
  { value: 'historico_execucao', label: 'Histórico de execução' },
] as const;

export const documentCategoryOptions = ['evidencia_fotografica', 'termo_instalacao', 'termo_retirada', 'anexo_tecnico', 'outro'];

export const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-400 dark:focus:border-brand-400';
export const areaClass = `${fieldClass} min-h-[88px] resize-y`;

export function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export function formatDateTimeLabel(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pt-BR');
}

export function toLocalDatetimeInput(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const tzOffset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - tzOffset).toISOString().slice(0, 16);
}

export function typeLabel(value: OrderType) {
  return orderTypeOptions.find((item) => item.value === value)?.label || value;
}
