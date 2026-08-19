'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Users, AlertTriangle, Building2, CheckCircle2, FileText, Wrench, CheckCircle, Clock, AlertCircle, Download, Plus, Trash2, Car, Coins, DollarSign, Flag, Mail, MessageCircle, PawPrint, Pencil, Printer, Receipt, Search } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid, FormSection, FormDivider } from '@/components/ui/form-field';
import { BillingDayInput, erroDiaVencimento } from '@/components/ui/billing-day-input';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { ExportButton } from '@/components/ui/export-button';
import { apiFetch, API_URL } from '@/lib/api';
import { entregarArquivo, nomeArquivoCliente } from '@/lib/arquivo';
import { enviarBoletoEmail, enviarBoletoWhats } from '@/lib/boleto-mensagem';
import { fetchAddressByCep } from '@/lib/cep';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';

type ClientStatus = 'ativo' | 'inativo' | 'inadimplente' | 'suspenso';
type ClientType = 'pf' | 'pj';
type ReviewStatus = 'enviado' | 'em_analise' | 'aprovado' | 'rejeitado' | 'reenvio_solicitado';

type ContactItem = { name: string; phone: string; email: string; role: string };

type Client = {
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

type ClientDocument = {
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

type VehicleSummary = { id: number; client_id: number; plate: string; status: string };

type VehicleDetailed = {
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

type BillingItem = {
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

type CarneItem = {
  lote_id: number;
  criado_em?: string | null;
  parcelas: number;
  parcelas_registradas: number;
  total: number;
  status: string;
};

type IntervContract = { id: number; client_name?: string | null; vehicle_plate?: string | null; plan_name?: string | null; status: string; monthly_value?: number | null };

type BillingChange = {
  id: number;
  field_name: string;
  previous_value?: string | null;
  new_value?: string | null;
  justification: string;
  created_at?: string | null;
};

type NfseItem = {
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

type TimelineContract = { id: number; start_date: string; plan_name?: string | null; status: string };
type TimelineOrder    = { id: number; number: string; type: string; status: string; executed_at?: string | null; scheduled_at?: string | null; created_at?: string | null; vehicle_plate?: string | null };
type TimelineBilling  = { id: number; amount: number; due_date: string; payment_date?: string | null; status: string; title?: string | null; plan_name?: string | null };

type TimelineEvent = {
  key: string;
  date: string;
  kind: 'contract' | 'os' | 'billing_paid' | 'billing_overdue' | 'billing_pending';
  title: string;
  subtitle: string;
};

function formatCurrency(value: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0);
}

function orderTypeLabel(type: string) {
  return ({ instalacao: 'Instalação', manutencao: 'Manutenção', retirada: 'Retirada', visita_tecnica: 'Visita técnica' } as Record<string, string>)[type] ?? type;
}

const emptyContact: ContactItem = { name: '', phone: '', email: '', role: '' };

type ClientFormState = {
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

const initialForm: ClientFormState = {
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

const documentCategoryOptions = ['cnh', 'rg', 'cpf', 'contrato', 'comprovante_endereco', 'cartao_cnpj', 'contrato_social', 'outro'];

/** Registro de contrato do cliente (para listar/excluir na modal de contratos). */
type ContractSheetItem = {
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

/**
 * Situação do contrato. Só entra "Em vigor" depois de assinado (o contrato
 * assinado é enviado no bloco abaixo); antes disso fica "Aguardando assinatura".
 */
function contractSituacao(c: ContractSheetItem): { label: string; variant: 'success' | 'warning' | 'danger' } {
  if (c.status === 'cancelado' || c.status === 'encerrado') return { label: 'Cancelado', variant: 'warning' };
  if (!c.signed) return { label: 'Aguardando assinatura', variant: 'warning' };
  const hoje = new Date().toISOString().slice(0, 10);
  if (c.end_date && c.end_date < hoje) return { label: 'Vencido', variant: 'danger' };
  return { label: 'Em vigor', variant: 'success' };
}

/** "enviado por Fulano em 12/08/2026" — metadados do anexo, quando houver. */
function envioMeta(doc: { uploaded_by?: string | null; created_at?: string | null }): string | null {
  if (!doc.uploaded_by && !doc.created_at) return null;
  const quem = doc.uploaded_by ? `enviado por ${doc.uploaded_by}` : 'enviado';
  const quando = doc.created_at ? ` em ${new Date(doc.created_at).toLocaleDateString('pt-BR')}` : '';
  return `${quem}${quando}`;
}
const fileInputClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white file:mr-4 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white dark:file:bg-brand-500';

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function parseExtraEmails(value: string) {
  return value.split(/[,;\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
}

// Botão de ação colorido — badges quadrados no código semântico do sistema de referência
const COLOR_CLASSES: Record<string, string> = {
  purple: 'bg-[#7952B3] text-white hover:brightness-110',   // veículos do cliente
  yellow: 'bg-[#FFC107] text-white hover:brightness-105',   // veículos como interveniente
  green:  'bg-[#28A745] text-white hover:brightness-110',   // central financeira / boletos
  white:  'border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300', // NF / documentação
  teal:   'bg-[#20C997] text-white hover:brightness-110',   // imprimir ficha de adesão
  blue:   'bg-[#17A2B8] text-white hover:brightness-110',   // editar
  red:    'bg-[#DC3545] text-white hover:brightness-110',   // excluir
  slate:  'bg-slate-500 text-white hover:bg-slate-600',
};

function ActionBtn({ color, icon: Icon, title, onClick }: { color: string; icon: React.ElementType; title: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`flex h-7 w-7 items-center justify-center rounded text-xs transition-colors ${COLOR_CLASSES[color] ?? COLOR_CLASSES.slate}`}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}

// Cabeçalho de coluna ordenável (padrão do sistema de referência)
type ClientSortField = 'id' | 'name' | 'trade_name' | 'cpf_cnpj' | 'status';
type ClientSort = { field: ClientSortField; dir: 'asc' | 'desc' };

function SortTh({
  field, label, sort, onSort, className,
}: {
  field: ClientSortField;
  label: string;
  sort: ClientSort;
  onSort: (f: ClientSortField) => void;
  className?: string;
}) {
  const active = sort.field === field;
  return (
    <th
      onClick={() => onSort(field)}
      className={`cursor-pointer select-none px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-700 dark:hover:text-slate-300 ${className ?? ''}`}
    >
      {label}{' '}
      <span className={active ? 'text-brand-600 dark:text-brand-400' : 'text-slate-300 dark:text-slate-600'}>
        {active ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </th>
  );
}

function cardKeyHandler(event: React.KeyboardEvent<HTMLElement>, callback: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    callback();
  }
}

export default function ClientesPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';
  // Ações financeiras (boletos, interveniente, NFS-e, ficha) usam endpoints
  // restritos a admin/financeiro — esconder do operacional evita 403 no clique
  const canFinance = !!user && (user.role === 'admin' || user.role === 'financeiro');

  const [clients, setClients] = useState<Client[]>([]);
  const [vehicleSummaries, setVehicleSummaries] = useState<VehicleSummary[]>([]);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [clientDocuments, setClientDocuments] = useState<ClientDocument[]>([]);
  const [form, setForm] = useState<ClientFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<'cadastro' | 'historico' | 'documentos'>('cadastro');
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [docCategory, setDocCategory] = useState('cnh');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [clientTimeline, setClientTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);

  // Ordenação + paginação da tabela (padrão do sistema de referência)
  const [clientSort, setClientSort] = useState<ClientSort>({ field: 'name', dir: 'asc' });
  const [pageSize, setPageSize] = useState(10);

  function toggleClientSort(field: ClientSortField) {
    setClientSort((prev) =>
      prev.field === field ? { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { field, dir: 'asc' },
    );
  }

  const sortedClients = useMemo(() => {
    const { field, dir } = clientSort;
    const factor = dir === 'asc' ? 1 : -1;
    return [...clients].sort((a, b) => {
      if (field === 'id') return (a.id - b.id) * factor;
      const av = String((a as unknown as Record<string, unknown>)[field] ?? '').toLowerCase();
      const bv = String((b as unknown as Record<string, unknown>)[field] ?? '').toLowerCase();
      return av.localeCompare(bv, 'pt-BR') * factor;
    });
  }, [clients, clientSort]);

  const pg = usePagination(sortedClients, pageSize);

  // Modal "Veículos vinculados ao cliente"
  const [vehiclesModalOpen, setVehiclesModalOpen] = useState(false);
  const [vehiclesModalClient, setVehiclesModalClient] = useState<Client | null>(null);
  const [vehiclesDetailed, setVehiclesDetailed] = useState<VehicleDetailed[]>([]);
  const [vehiclesModalLoading, setVehiclesModalLoading] = useState(false);

  // Modal "Boletos do cliente"
  const [billingsModalOpen, setBillingsModalOpen] = useState(false);
  const [billingsModalClient, setBillingsModalClient] = useState<Client | null>(null);
  const [clientBillings, setClientBillings] = useState<BillingItem[]>([]);
  const [billingsLoading, setBillingsLoading] = useState(false);
  const [billingSummaryExpanded, setBillingSummaryExpanded] = useState(false);

  // Seleção múltipla de boletos (soma para pagamento em lote)
  const [selectedBillingIds, setSelectedBillingIds] = useState<number[]>([]);
  const [gerandoCarne, setGerandoCarne] = useState(false);
  // Carnês já gerados do cliente (reabrir/baixar)
  const [carnes, setCarnes] = useState<CarneItem[]>([]);

  // Unificação de boletos (negociação: N boletos abertos → 1 avulso)
  const [unifyOpen, setUnifyOpen] = useState(false);
  const [unifyForm, setUnifyForm] = useState({ due_date: '', amount: '', notes: '' });
  const [unifying, setUnifying] = useState(false);

  // Ações do modal de boletos (alterar / histórico)
  const [editBilling, setEditBilling] = useState<BillingItem | null>(null);
  const [editBillingForm, setEditBillingForm] = useState({ amount: '', due_date: '', justification: '' });
  const [savingBilling, setSavingBilling] = useState(false);
  const [historyBilling, setHistoryBilling] = useState<BillingItem | null>(null);
  const [billingChanges, setBillingChanges] = useState<BillingChange[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Modal "Veículos onde o cliente é interveniente financeiro"
  const [intervModalOpen, setIntervModalOpen] = useState(false);
  const [intervModalClient, setIntervModalClient] = useState<Client | null>(null);
  const [intervContracts, setIntervContracts] = useState<IntervContract[]>([]);
  const [intervLoading, setIntervLoading] = useState(false);

  // Modal "Ficha de adesão / contrato" (botão teal da impressora)
  const [contractSheetOpen, setContractSheetOpen] = useState(false);
  const [contractSheetClient, setContractSheetClient] = useState<Client | null>(null);
  const [contractSheetLoading, setContractSheetLoading] = useState(false);
  // Contrato assinado guardado nos documentos (categoria 'contrato')
  const [contractDocs, setContractDocs] = useState<ClientDocument[]>([]);
  const [contractSheetItems, setContractSheetItems] = useState<ContractSheetItem[]>([]);
  const [contractSignAlvo, setContractSignAlvo] = useState('');   // contrato que o assinado enviado coloca "em vigor"
  const [contractFile, setContractFile] = useState<File | null>(null);
  const [uploadingContract, setUploadingContract] = useState(false);
  const [contractCheck, setContractCheck] = useState<{ level: string; message: string } | null>(null);

  // Modal "Notas fiscais do cliente" (botão da patinha)
  const [nfseModalOpen, setNfseModalOpen] = useState(false);
  const [nfseModalClient, setNfseModalClient] = useState<Client | null>(null);
  const [clientNotas, setClientNotas] = useState<NfseItem[]>([]);
  const [nfseLoading, setNfseLoading] = useState(false);

  async function loadClients(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (typeFilter) query.set('type', typeFilter);
      query.set('limit', '200');
      const [clientResponse, vehicleResponse] = await Promise.all([
        apiFetch<Client[]>(`/clients?${query.toString()}`, {}, currentToken),
        apiFetch<VehicleSummary[]>('/vehicles?limit=500', {}, currentToken),
      ]);
      setClients(clientResponse);
      setVehicleSummaries(vehicleResponse);
      if (selectedClient) {
        const refreshed = clientResponse.find((item) => item.id === selectedClient.id) || null;
        setSelectedClient(refreshed);
      }
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadClientDocuments(currentToken: string, clientId: number) {
    try {
      const response = await apiFetch<ClientDocument[]>(`/clients/${clientId}/documents`, {}, currentToken);
      setClientDocuments(response);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function loadClientTimeline(currentToken: string, clientId: number) {
    setTimelineLoading(true);
    try {
      const [contracts, orders, bills] = await Promise.allSettled([
        apiFetch<TimelineContract[]>(`/contracts?client_id=${clientId}`, {}, currentToken),
        apiFetch<TimelineOrder[]>(`/service-orders?client_id=${clientId}&limit=30`, {}, currentToken),
        apiFetch<TimelineBilling[]>(`/billings?client_id=${clientId}&limit=30`, {}, currentToken),
      ]);

      const events: TimelineEvent[] = [];

      if (contracts.status === 'fulfilled') {
        for (const c of contracts.value) {
          events.push({
            key: `contract-${c.id}`,
            date: c.start_date,
            kind: 'contract',
            title: `Contrato iniciado — ${c.plan_name || 'Plano'}`,
            subtitle: `Status: ${c.status}`,
          });
        }
      }

      if (orders.status === 'fulfilled') {
        for (const o of orders.value) {
          events.push({
            key: `os-${o.id}`,
            date: o.executed_at || o.scheduled_at || o.created_at || '',
            kind: 'os',
            title: `OS #${o.number} — ${orderTypeLabel(o.type)}`,
            subtitle: o.vehicle_plate ? `Veículo: ${o.vehicle_plate} • ${o.status}` : `Status: ${o.status}`,
          });
        }
      }

      if (bills.status === 'fulfilled') {
        for (const b of bills.value) {
          const kind: TimelineEvent['kind'] =
            b.status === 'paga' ? 'billing_paid' :
            b.status === 'vencida' ? 'billing_overdue' : 'billing_pending';
          events.push({
            key: `billing-${b.id}`,
            date: b.payment_date || b.due_date,
            kind,
            title: `${b.title || b.plan_name || 'Cobrança'} — ${formatCurrency(b.amount)}`,
            subtitle: b.payment_date ? `Pago em ${b.payment_date}` : `Venc. ${b.due_date}`,
          });
        }
      }

      const sorted = events
        .filter((e) => e.date)
        .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
        .slice(0, 25);

      setClientTimeline(sorted);
    } catch {
      setClientTimeline([]);
    } finally {
      setTimelineLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadClients(token);
  }, [token]);

  // Busca/filtros dinâmicos: recarrega ao parar de digitar ou ao trocar um
  // filtro, sem precisar clicar em "Filtrar".
  const searchDebounced = useDebouncedValue(search);
  useEffectSkipFirst(() => {
    if (token) loadClients(token);
  }, [searchDebounced, statusFilter, typeFilter]);

  async function openVehiclesModal(client: Client) {
    setVehiclesModalClient(client);
    setVehiclesModalOpen(true);
    setVehiclesModalLoading(true);
    try {
      const [vehs, trackers] = await Promise.all([
        apiFetch<{ id: number; plate: string; type?: string | null; brand?: string | null; model?: string | null; status: string }[]>(
          `/vehicles?client_id=${client.id}&limit=100`, {}, token!
        ).catch(() => []),
        apiFetch<{ id: number; vehicle_id?: number | null; imei: string; brand?: string | null; model?: string | null; active_plan_name?: string | null }[]>(
          `/trackers?client_id=${client.id}&limit=100`, {}, token!
        ).catch(() => []),
      ]);
      const enriched: VehicleDetailed[] = vehs.map((v) => {
        const t = trackers.find((tr) => tr.vehicle_id === v.id);
        return {
          ...v,
          tracker_imei: t?.imei ?? null,
          tracker_brand: t?.brand ?? null,
          tracker_model: t?.model ?? null,
          tracker_plan: t?.active_plan_name ?? null,
        };
      });
      setVehiclesDetailed(enriched);
    } finally {
      setVehiclesModalLoading(false);
    }
  }

  async function openBillingsModal(client: Client) {
    setBillingsModalClient(client);
    setBillingsModalOpen(true);
    setSelectedBillingIds([]);
    setBillingsLoading(true);
    try {
      const [data, cs] = await Promise.all([
        apiFetch<BillingItem[]>(`/billings?client_id=${client.id}&limit=100`, {}, token!).catch(() => []),
        apiFetch<CarneItem[]>(`/boletos/carne?client_id=${client.id}`, {}, token!).catch(() => []),
      ]);
      setClientBillings(data);
      setCarnes(cs);
    } finally {
      setBillingsLoading(false);
    }
  }

  async function reloadCarnes() {
    if (!token || !billingsModalClient) return;
    const cs = await apiFetch<CarneItem[]>(`/boletos/carne?client_id=${billingsModalClient.id}`, {}, token).catch(() => []);
    setCarnes(cs);
  }

  async function baixarCarne(loteId: number) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/carne/${loteId}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      entregarArquivo(await resp.blob(), `carne-${loteId}.pdf`, { emNovaAba: true });
    } catch (err) {
      alert(parseError(err));
    }
  }

  // Valor com multa/juros calculado pelo BACKEND (fonte única — cláusula 4.3
  // do contrato). Vem no campo valor_com_juros do /billings.
  function valorComJuros(b: BillingItem): number | null {
    return b.valor_com_juros ?? null;
  }

  async function reloadClientBillings() {
    if (!token || !billingsModalClient) return;
    const data = await apiFetch<BillingItem[]>(
      `/billings?client_id=${billingsModalClient.id}&limit=100`, {}, token
    ).catch(() => []);
    setClientBillings(data);
  }

  function openUnifyModal() {
    const sel = clientBillings.filter((b) => selectedBillingIds.includes(b.id));
    const soma = sel.reduce((s, b) => s + b.amount, 0);
    setUnifyForm({ due_date: '', amount: soma.toFixed(2), notes: '' });
    setUnifyOpen(true);
  }

  /**
   * Gera o carnê das cobranças selecionadas: registra o lote na Ailos,
   * aguarda o processamento (assíncrono) e baixa o PDF. As parcelas são as
   * cobranças em aberto do mesmo cliente, na ordem selecionada.
   */
  async function gerarCarne() {
    if (!token || selectedBillingIds.length < 2) return;
    const qtd = selectedBillingIds.length;
    if (!confirm(`Gerar o carnê registra ${qtd} boletos reais na Ailos (um por parcela). Continuar?`)) return;
    setGerandoCarne(true);
    try {
      // 1. registra o carnê (assíncrono — devolve o lote com ticket)
      const lote = await apiFetch<{ id: number; ticket: string; status: string }>(
        '/ailos/carne/lote',
        { method: 'POST', body: JSON.stringify({ billing_ids: selectedBillingIds }) },
        token,
      );
      // 2. aguarda o lote sair de "processing" (cada consulta atualiza os boletos)
      let status = lote.status;
      for (let i = 0; i < 15 && status === 'processing'; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await apiFetch<{ status: string }>(`/ailos/lotes/${lote.ticket}`, {}, token).catch(() => ({ status } as { status: string }));
        status = st.status;
      }
      // O carnê já foi REGISTRADO no passo 1. Se ainda processa, não insistir no
      // download (evita re-registro, que duplicaria boletos).
      if (status === 'processing') {
        setSelectedBillingIds([]);
        await reloadCarnes();
        setFeedback(`Carnê registrado (lote #${lote.id}). As parcelas ainda estão sendo processadas na Ailos — ele já aparece em "Carnês gerados"; baixe o PDF em instantes.`);
        return;
      }
      // 3. baixa o PDF do carnê (só as parcelas já registradas entram)
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/carne/${lote.id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(`Carnê registrado (lote #${lote.id}), mas o PDF ainda não saiu: ${detalhe}`);
      }
      entregarArquivo(await resp.blob(), `carne-${lote.id}.pdf`, { emNovaAba: true });
      setSelectedBillingIds([]);
      setFeedback(`Carnê gerado com ${qtd} parcela(s).`);
      await Promise.all([reloadClientBillings(), reloadCarnes()]);
    } catch (err) {
      alert(parseError(err));
    } finally {
      setGerandoCarne(false);
    }
  }

  async function saveUnify() {
    if (!token || selectedBillingIds.length < 2) return;
    if (!unifyForm.due_date) { alert('Informe o vencimento do boleto único.'); return; }
    setUnifying(true);
    try {
      const nova = await apiFetch<BillingItem>('/billings/unificar', {
        method: 'POST',
        body: JSON.stringify({
          billing_ids: selectedBillingIds,
          due_date: unifyForm.due_date,
          amount: unifyForm.amount ? Number(unifyForm.amount) : undefined,
          notes: unifyForm.notes.trim() || undefined,
        }),
      }, token);
      setUnifyOpen(false);
      setSelectedBillingIds([]);
      setFeedback(`Boleto único #${nova.id} criado. As cobranças originais foram canceladas.`);
      await reloadClientBillings();
    } catch (err) {
      alert(parseError(err));
    } finally {
      setUnifying(false);
    }
  }

  function openEditBilling(b: BillingItem) {
    setEditBilling(b);
    setEditBillingForm({ amount: String(b.amount), due_date: b.due_date, justification: '' });
  }

  async function saveEditBilling() {
    if (!token || !editBilling) return;
    const payload: Record<string, unknown> = {};
    if (Number(editBillingForm.amount) !== editBilling.amount) payload.amount = Number(editBillingForm.amount);
    if (editBillingForm.due_date !== editBilling.due_date) payload.due_date = editBillingForm.due_date;
    if (Object.keys(payload).length === 0) { setEditBilling(null); return; }
    const justification = editBillingForm.justification.trim();
    if (!justification) { alert('Informe a justificativa da alteração.'); return; }
    payload.justification = justification;
    setSavingBilling(true);
    try {
      await apiFetch(`/billings/${editBilling.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
      setEditBilling(null);
      await reloadClientBillings();
    } catch (err) {
      alert(parseError(err));
    } finally {
      setSavingBilling(false);
    }
  }

  async function openBillingHistory(b: BillingItem) {
    if (!token) return;
    setHistoryBilling(b);
    setHistoryLoading(true);
    try {
      const logs = await apiFetch<BillingChange[]>(`/billings/${b.id}/changes`, {}, token).catch(() => []);
      setBillingChanges(logs);
    } finally {
      setHistoryLoading(false);
    }
  }

  // Envio com template das Configurações — lógica compartilhada em lib/boleto-mensagem
  async function sendBoletoEmail(b: BillingItem) {
    if (!token || !billingsModalClient) return;
    try {
      await enviarBoletoEmail(b, billingsModalClient, token);
    } catch (err) {
      alert(parseError(err));
    }
  }

  async function sendBoletoWhats(b: BillingItem) {
    if (!token || !billingsModalClient) return;
    try {
      await enviarBoletoWhats(b, billingsModalClient, token);
    } catch (err) {
      alert(parseError(err));
    }
  }

  /** Abre o PDF da nota fiscal no navegador (montado a partir do XML). */
  async function abrirNotaPdf(billingId: number) {
    if (!token) return;
    try {
      const resp = await fetch(
        `${API_URL.replace(/\/+$/, '')}/nfse/${billingId}/danfse-local`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      entregarArquivo(await resp.blob(), `nfse-${billingId}.pdf`, { emNovaAba: true });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao abrir a nota fiscal');
    }
  }

  async function baixarComprovante(b: BillingItem) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/billings/${b.id}/receipt`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status} ao gerar o comprovante`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao gerar comprovante');
    }
  }

  async function baixarBoletoPdf(b: BillingItem) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/${b.id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Nome do arquivo: cliente + vencimento (ex.: "EUNICE SOUSA SIMAS 28-08-2026.pdf").
      a.download = `${nomeArquivoCliente(billingsModalClient?.name, b.due_date)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao baixar boleto');
    }
  }

  async function openIntervenienteModal(client: Client) {
    setIntervModalClient(client);
    setIntervModalOpen(true);
    setIntervLoading(true);
    try {
      const data = await apiFetch<IntervContract[]>(
        `/contracts?interveniente_client_id=${client.id}&limit=100`, {}, token!
      ).catch(() => []);
      setIntervContracts(data);
    } finally {
      setIntervLoading(false);
    }
  }

  async function openNfseModal(client: Client) {
    setNfseModalClient(client);
    setNfseModalOpen(true);
    setNfseLoading(true);
    try {
      const data = await apiFetch<NfseItem[]>(
        `/nfse?client_id=${client.id}&limit=100`, {}, token!
      ).catch(() => []);
      setClientNotas(data);
    } finally {
      setNfseLoading(false);
    }
  }

  // Abre a modal com todos os contratos do cliente (vigência, situação,
  // ver/imprimir e excluir). Substitui a impressão direta do mais recente.
  async function openContractSheet(client: Client) {
    setContractSheetClient(client);
    setContractSheetOpen(true);
    setContractSheetLoading(true);
    setContractFile(null);
    setContractCheck(null);
    setContractSheetItems([]);
    try {
      const [docs, contratos] = await Promise.all([
        apiFetch<ClientDocument[]>(`/clients/${client.id}/documents`, {}, token!).catch(() => []),
        apiFetch<ContractSheetItem[]>(`/contracts?client_id=${client.id}&limit=200`, {}, token!).catch(() => []),
      ]);
      setContractDocs(docs.filter((d) => d.category === 'contrato'));
      setContractSheetItems(contratos);
      // Sugere colocar "em vigor" o primeiro contrato ainda não assinado.
      const pendente = contratos.find((c) => !c.signed && c.status !== 'cancelado' && c.status !== 'encerrado');
      setContractSignAlvo(pendente ? String(pendente.id) : '');
    } finally {
      setContractSheetLoading(false);
    }
  }

  // Envia o contrato assinado já na categoria certa ('contrato'), direto da
  // modal — sem depender de o operador lembrar de trocar a categoria na edição.
  async function uploadSignedContract() {
    if (!token || !contractSheetClient || !contractFile) return;
    setUploadingContract(true);
    try {
      // 1. Confere o arquivo (não bloqueante). Se cair, segue o upload assim mesmo.
      let verdict: { level: string; message: string } | null = null;
      try {
        const vbody = new FormData();
        vbody.append('file', contractFile);
        verdict = await apiFetch<{ level: string; message: string }>(
          `/contracts/validate-signed?client_id=${contractSheetClient.id}`,
          { method: 'POST', body: vbody }, token,
        );
      } catch { verdict = null; }
      setContractCheck(verdict && verdict.message ? verdict : null);
      // "em branco" (não preenchido) e "mismatch" (arquivo errado) pedem
      // confirmação — fica opcional seguir. Escaneamento ilegível só avisa.
      if (verdict && (verdict.level === 'blank' || verdict.level === 'mismatch')) {
        if (!window.confirm(`${verdict.message}\n\nDeseja enviar mesmo assim?`)) {
          setUploadingContract(false);
          return;
        }
      }
      // 2. Sobe o arquivo já na categoria certa.
      const body = new FormData();
      body.append('category', 'contrato');
      body.append('files', contractFile);
      await apiFetch(`/clients/${contractSheetClient.id}/documents`, { method: 'POST', body }, token);
      setContractFile(null);
      const docs = await apiFetch<ClientDocument[]>(`/clients/${contractSheetClient.id}/documents`, {}, token).catch(() => []);
      setContractDocs(docs.filter((d) => d.category === 'contrato'));
      setContractSheetClient((prev) => (prev ? { ...prev, contrato_armazenado: true } : prev));
      // 3. O contrato escolhido passa a valer ("em vigor") — assinado recebido.
      if (contractSignAlvo) {
        await apiFetch(`/contracts/${contractSignAlvo}`, { method: 'PUT', body: JSON.stringify({ signed: true }) }, token).catch(() => null);
        const contratos = await apiFetch<ContractSheetItem[]>(`/contracts?client_id=${contractSheetClient.id}&limit=200`, {}, token).catch(() => []);
        setContractSheetItems(contratos);
        const pendente = contratos.find((c) => !c.signed && c.status !== 'cancelado' && c.status !== 'encerrado');
        setContractSignAlvo(pendente ? String(pendente.id) : '');
      }
      await loadClients(token); // atualiza o selo "armazenado" na listagem
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao enviar o contrato assinado');
    } finally {
      setUploadingContract(false);
    }
  }

  async function removeContractDoc(id: number) {
    if (!token || !contractSheetClient) return;
    if (!window.confirm('Remover este contrato assinado?')) return;
    try {
      await apiFetch(`/clients/${contractSheetClient.id}/documents/${id}`, { method: 'DELETE' }, token);
      const docs = await apiFetch<ClientDocument[]>(`/clients/${contractSheetClient.id}/documents`, {}, token).catch(() => []);
      const contratoDocs = docs.filter((d) => d.category === 'contrato');
      setContractDocs(contratoDocs);
      setContractSheetClient((prev) => (prev ? { ...prev, contrato_armazenado: contratoDocs.length > 0 } : prev));
      await loadClients(token);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao remover o contrato assinado');
    }
  }

  // Abre o PDF do contrato (registro) gerado a partir dos dados dele.
  async function baixarContrato(id: number) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/contracts/${id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status} ao gerar o contrato`);
      entregarArquivo(await resp.blob(), `contrato-${id}.pdf`, { emNovaAba: true });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao abrir o contrato');
    }
  }

  // Cancela/exclui um contrato (soft delete: vira "cancelado" e some das listagens).
  async function excluirContrato(id: number) {
    if (!token) return;
    if (!window.confirm('Cancelar/excluir este contrato? Ele passa a "cancelado" e some das listagens.')) return;
    try {
      await apiFetch(`/contracts/${id}`, { method: 'DELETE' }, token);
      setContractSheetItems((prev) => prev.filter((c) => c.id !== id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao excluir o contrato');
    }
  }

  async function deleteClient(client: Client) {
    if (!token) return;
    const ok = window.confirm(
      `Excluir o cliente "${client.name}"?\n\nO cadastro será inativado (soft delete) e some das listagens.`
    );
    if (!ok) return;
    try {
      await apiFetch(`/clients/${client.id}`, { method: 'DELETE' }, token);
      setFeedback('Cliente removido.');
      await loadClients(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  useEffect(() => {
    if (!token || !selectedClient) {
      setClientDocuments([]);
      setClientTimeline([]);
      return;
    }
    loadClientDocuments(token, selectedClient.id);
    loadClientTimeline(token, selectedClient.id);
  }, [token, selectedClient?.id]);

  const stats = useMemo(() => ({
    total: clients.length,
    active: clients.filter((item) => item.status === 'ativo').length,
    delinquent: clients.filter((item) => item.status === 'inadimplente').length,
    company: clients.filter((item) => item.type === 'pj').length,
  }), [clients]);

  const vehiclesByClient = useMemo(() => vehicleSummaries.reduce<Record<number, VehicleSummary[]>>((acc, vehicle) => {
    if (!acc[vehicle.client_id]) acc[vehicle.client_id] = [];
    acc[vehicle.client_id].push(vehicle);
    return acc;
  }, {}), [vehicleSummaries]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
    setDocFiles([]);
    setDocCategory('cnh');
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(client: Client) {
    setSelectedClient(client);
    setIsEditing(true);
    setModalError('');
    setForm({
      name: client.name || '',
      cpf_cnpj: formatCpfCnpj(client.cpf_cnpj || ''),
      type: client.type || 'pf',
      status: client.status || 'ativo',
      email: client.email || '',
      extra_emails: (client.extra_emails || []).join('\n'),
      phone: client.phone ? formatPhone(client.phone) : '',
      contacts: (client.contacts || []).map((c) => ({ name: c.name || '', phone: c.phone || '', email: c.email || '', role: c.role || '' })),
      zip_code: client.zip_code ? formatZipCode(client.zip_code) : '',
      address_line: client.address_line || '',
      address_number: client.address_number || '',
      address_complement: client.address_complement || '',
      neighborhood: client.neighborhood || '',
      city: client.city || '',
      state: client.state || '',
      notes: client.notes || '',
      billing_day: client.billing_day != null ? String(client.billing_day) : '',
      rg_ie: client.rg_ie || '',
      birth_date: client.birth_date || '',
      em1_name: client.emergency_contacts?.[0]?.name || '',
      em1_phone: client.emergency_contacts?.[0]?.phone || '',
      em1_mobile: client.emergency_contacts?.[0]?.mobile || '',
      em2_name: client.emergency_contacts?.[1]?.name || '',
      em2_phone: client.emergency_contacts?.[1]?.phone || '',
      em2_mobile: client.emergency_contacts?.[1]?.mobile || '',
      boleto_format: client.boleto_format || 'unico',
      boleto_fee: client.boleto_fee || 'nao',
      issue_invoice: client.issue_invoice || 'sim',
      tributacao: client.tributacao || 'dentro_municipio',
      iss_retido: client.iss_retido || 'nao',
      optante_simples: client.optante_simples || 'sim',
      delivery_method: client.delivery_method || 'email',
      send_boleto_whatsapp: !!client.send_boleto_whatsapp,
      trade_name: client.trade_name || '',
    });
    setDocFiles([]);
    setModalOpen(true);
  }

  function addContact() {
    setForm((prev) => ({ ...prev, contacts: [...prev.contacts, { ...emptyContact }] }));
  }

  function removeContact(index: number) {
    setForm((prev) => ({ ...prev, contacts: prev.contacts.filter((_, i) => i !== index) }));
  }

  function updateContact(index: number, field: keyof ContactItem, value: string) {
    setForm((prev) => {
      const contacts = [...prev.contacts];
      contacts[index] = { ...contacts[index], [field]: value };
      return { ...prev, contacts };
    });
  }

  async function downloadTimelinePdf() {
    if (!token || !selectedClient) return;
    try {
      const response = await fetch(`${API_URL.replace(/\/+$/, '')}/clients/${selectedClient.id}/timeline-pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Erro ao gerar PDF');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `timeline-${selectedClient.name}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(parseError(err));
    }
  }

  function handleChange(field: keyof ClientFormState, value: string) {
    let nextValue = value;
    if (field === 'cpf_cnpj') nextValue = formatCpfCnpj(value);
    if (field === 'phone') nextValue = formatPhone(value);
    if (field === 'zip_code') nextValue = formatZipCode(value);
    if (field === 'state') nextValue = value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
    setForm((prev) => ({ ...prev, [field]: nextValue }));
  }

  async function fillAddressFromCep(rawCep: string) {
    const cep = onlyDigits(rawCep);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    try {
      const address = await fetchAddressByCep(cep);
      if (address) {
        setForm((prev) => ({
          ...prev,
          zip_code: formatZipCode(cep),
          address_line: address.address_line || prev.address_line,
          neighborhood: address.neighborhood || prev.neighborhood,
          city: address.city || prev.city,
          state: address.state || prev.state,
        }));
      }
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setLookingUpCep(false);
    }
  }

  async function submitClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setModalError('');
    setFeedback('');
    try {
      const cleanContacts = form.contacts
        .filter((c) => c.name.trim())
        .map((c) => ({ name: c.name.trim(), phone: c.phone.trim() || null, email: c.email.trim() || null, role: c.role.trim() || null }));

      const emergencyContacts = [
        { name: form.em1_name.trim(), phone: form.em1_phone.trim(), mobile: form.em1_mobile.trim() },
        { name: form.em2_name.trim(), phone: form.em2_phone.trim(), mobile: form.em2_mobile.trim() },
      ]
        .filter((e) => e.name || e.phone || e.mobile)
        .map((e) => ({ name: e.name || null, phone: e.phone || null, mobile: e.mobile || null }));

      const payload = {
        name: form.name.trim(),
        cpf_cnpj: onlyDigits(form.cpf_cnpj),
        type: form.type,
        status: form.status,
        email: normalizeEmail(form.email) || null,
        extra_emails: parseExtraEmails(form.extra_emails),
        phone: onlyDigits(form.phone) || null,
        contacts: cleanContacts.length ? cleanContacts : null,
        zip_code: onlyDigits(form.zip_code) || null,
        address_line: form.address_line.trim() || null,
        address_number: form.address_number.trim() || null,
        address_complement: form.address_complement.trim() || null,
        neighborhood: form.neighborhood.trim() || null,
        city: form.city.trim() || null,
        state: form.state.trim() || null,
        notes: form.notes.trim() || null,
        billing_day: form.billing_day ? Number(form.billing_day) : null,
        rg_ie: form.rg_ie.trim() || null,
        birth_date: form.birth_date || null,
        emergency_contacts: emergencyContacts.length ? emergencyContacts : null,
        boleto_format: form.boleto_format || null,
        boleto_fee: form.boleto_fee || null,
        issue_invoice: form.issue_invoice || null,
        tributacao: form.tributacao || null,
        iss_retido: form.iss_retido || null,
        optante_simples: form.optante_simples || null,
        delivery_method: form.delivery_method || null,
        send_boleto_whatsapp: form.send_boleto_whatsapp,
        trade_name: form.trade_name.trim() || null,
      };

      const saved = isEditing && selectedClient
        ? await apiFetch<Client>(`/clients/${selectedClient.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<Client>('/clients', { method: 'POST', body: JSON.stringify(payload) }, token);

      if (docFiles.length) {
        const body = new FormData();
        body.append('category', docCategory);
        docFiles.forEach((file) => body.append('files', file));
        await apiFetch(`/clients/${saved.id}/documents`, { method: 'POST', body }, token);
      }

      setFeedback(isEditing ? 'Cliente atualizado com sucesso.' : 'Cliente cadastrado com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadClients(token);
      setSelectedClient(saved);
      await loadClientDocuments(token, saved.id);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function uploadDocuments() {
    if (!token || !selectedClient || !canEdit || !docFiles.length) return;
    setUploading(true);
    setError('');
    setFeedback('');
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/clients/${selectedClient.id}/documents`, { method: 'POST', body }, token);
      setFeedback('Documentos enviados com sucesso.');
      setDocFiles([]);
      await loadClientDocuments(token, selectedClient.id);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function deleteDocument(documentId: number) {
    if (!token || !selectedClient || !canEdit) return;
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/clients/${selectedClient.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      await loadClientDocuments(token, selectedClient.id);
      setFeedback('Documento removido com sucesso.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function reviewDocument(documentId: number, status: ReviewStatus) {
    if (!token || !selectedClient || !canEdit) return;
    const notes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/clients/${selectedClient.id}/documents/${documentId}/review`, {
        method: 'POST',
        body: JSON.stringify({ review_status: status, review_notes: notes || null }),
      }, token);
      await loadClientDocuments(token, selectedClient.id);
      setFeedback('Status do documento atualizado.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Clientes" description="Gestão da base cadastral com formulário em modal, documentação centralizada e visão rápida dos veículos vinculados.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}

      {/* Base de clientes primeiro; os indicadores ficam abaixo (pedido do cliente) */}
      <section>
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Base de clientes"
            description="Pesquise e gerencie a carteira completa de clientes."
            actions={
              <div className="flex items-center gap-2">
                {token && <ExportButton path="exports/clients" basename="clientes" token={token} params={{ status: statusFilter, type: typeFilter }} />}
                {canEdit && <Button type="button" onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button>}
              </div>
            }
          />
          {/* Filtros à esquerda, busca à direita. Os controles têm largura fixa
              e `shrink-0` para não esticarem/espremerem conforme o conteúdo. */}
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <Select
              value={String(pageSize)}
              onChange={(e) => { setPageSize(Number(e.target.value)); pg.setPage(1); }}
              className="w-[72px] shrink-0"
              aria-label="Resultados por página"
            >
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </Select>
            <span className="shrink-0 whitespace-nowrap text-sm text-slate-500 dark:text-slate-400">
              por página
            </span>

            <Select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-44 shrink-0"
              aria-label="Filtrar por status"
            >
              <option value="">Todos os status</option>
              <option value="ativo">Ativo</option>
              <option value="inativo">Inativo</option>
              <option value="inadimplente">Inadimplente</option>
              <option value="suspenso">Suspenso</option>
            </Select>
            <Select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="w-40 shrink-0"
              aria-label="Filtrar por tipo"
            >
              <option value="">Todos os tipos</option>
              <option value="pf">Pessoa física</option>
              <option value="pj">Pessoa jurídica</option>
            </Select>
            <Button
              type="button"
              variant="secondary"
              className="shrink-0"
              onClick={() => token && loadClients(token)}
              disabled={loading}
            >
              {loading ? 'Atualizando…' : 'Atualizar'}
            </Button>

            <div className="relative ml-auto w-full sm:w-80">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <Input
                placeholder="Pesquisar por nome, CPF/CNPJ ou e-mail"
                value={search}
                onChange={(e) => { setSearch(e.target.value); pg.setPage(1); }}
                className="w-full pl-9"
              />
            </div>
          </div>

          <div className="mt-4">
            {loading ? (
              <TableSkeleton rows={7} cols={5} />
            ) : clients.length === 0 ? (
              <EmptyState icon={Users} title="Nenhum cliente encontrado" description="Ajuste os filtros ou cadastre o primeiro cliente." action={canEdit ? <Button onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button> : undefined} />
            ) : (
              <>
              <Table>
                <TableHead>
                  <SortTh field="id" label="Matrícula" sort={clientSort} onSort={toggleClientSort} className="w-24" />
                  <SortTh field="name" label="Nome" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="trade_name" label="Nome fantasia" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="cpf_cnpj" label="CPF/CNPJ" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="status" label="Situação" sort={clientSort} onSort={toggleClientSort} />
                  <Th className="w-28">Contrato</Th>
                  <Th className="w-40">Ações</Th>
                </TableHead>
                <TableBody>
                  {pg.slice.map((client) => {
                    const vehicles = vehiclesByClient[client.id] || [];
                    return (
                      <Tr key={client.id}>
                        <Td className="text-sm text-slate-500">{client.id}</Td>
                        <Td>
                          <p className="font-medium text-slate-900 dark:text-white">{client.name}</p>
                          <p className="text-xs text-slate-400">{client.type === 'pj' ? 'Pessoa Jurídica' : 'Pessoa Física'} · {vehicles.length} veículo(s)</p>
                        </Td>
                        <Td className="text-sm">{client.trade_name || '—'}</Td>
                        <Td>
                          <p className="font-mono text-xs">{formatCpfCnpj(client.cpf_cnpj)}</p>
                          <p className="text-xs text-slate-400">{client.email || (client.phone ? formatPhone(client.phone) : '')}</p>
                        </Td>
                        <Td>
                          <Badge variant={statusVariant(client.status)}>{statusLabel(client.status)}</Badge>
                        </Td>
                        <Td>
                          {client.contrato_armazenado
                            ? <Badge variant="success">Armazenado</Badge>
                            : <Badge variant="warning">Pendente</Badge>}
                        </Td>
                        <Td>
                          <div className="flex justify-end gap-1">
                            {/* 1. Roxo — veículos próprios do cliente */}
                            <ActionBtn color="purple" icon={Car} title="Veículos vinculados ao cliente" onClick={() => openVehiclesModal(client)} />
                            {canFinance && (
                              <>
                                {/* 2. Amarelo — veículos onde é interveniente financeiro */}
                                <ActionBtn color="yellow" icon={Coins} title="Veículos onde é interveniente financeiro" onClick={() => openIntervenienteModal(client)} />
                                {/* 3. Verde — central financeira / boletos */}
                                <ActionBtn color="green" icon={DollarSign} title="Central financeira / boletos do cliente" onClick={() => openBillingsModal(client)} />
                                {/* 4. Branco (patinha) — notas fiscais do cliente */}
                                <ActionBtn color="white" icon={PawPrint} title="Notas fiscais do cliente" onClick={() => openNfseModal(client)} />
                                {/* 5. Teal — ficha de adesão / contrato (abre a lista) */}
                                <ActionBtn color="teal" icon={Printer} title="Ficha de adesão / contrato" onClick={() => openContractSheet(client)} />
                              </>
                            )}
                            {canEdit && (
                              <>
                                {/* 6. Azul — editar */}
                                <ActionBtn color="blue" icon={Pencil} title="Editar cliente" onClick={() => openEditModal(client)} />
                                {/* 7. Vermelho — excluir/inativar */}
                                <ActionBtn color="red" icon={Trash2} title="Excluir cliente" onClick={() => deleteClient(client)} />
                              </>
                            )}
                          </div>
                        </Td>
                      </Tr>
                    );
                  })}
                </TableBody>
              </Table>

              {/* Rodapé: contagem + paginação (padrão do sistema de referência) */}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {pg.total === 0
                    ? 'Nenhum registro'
                    : `Mostrando de ${pg.start} até ${pg.end} de ${pg.total} registro(s)`}
                </p>
              </div>
              <Pagination {...pg} onPage={pg.setPage} className="mt-1" />
              </>
            )}
          </div>
        </Card>
      </section>

      {/* Indicadores da base (abaixo da tabela, conforme solicitado) */}
      <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Clientes cadastrados" value={stats.total}      hint="Base total disponível"           icon={<Users className="h-5 w-5" />} />
        <StatCard label="Clientes ativos"      value={stats.active}     hint="Cadastros em operação"  tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
        <StatCard label="Inadimplentes"        value={stats.delinquent} hint="Exigem ação do financeiro" tone="warning" icon={<AlertTriangle className="h-5 w-5" />} />
        <StatCard label="Empresas (PJ)"        value={stats.company}    hint="Cadastros PJ na base"    tone="brand"   icon={<Building2 className="h-5 w-5" />} />
      </section>

      {/* Modal de detalhes */}
      <Modal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedClient(null); }}
        title={selectedClient?.name ?? ''}
        subtitle="Detalhes do cliente"
        size="xl"
      >
        {selectedClient && (
          <div className="space-y-4">
            {/* Situação do cadastro + do contrato assinado, logo no topo */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={statusVariant(selectedClient.status)}>{statusLabel(selectedClient.status)}</Badge>
              {selectedClient.contrato_armazenado
                ? <Badge variant="success">Contrato armazenado</Badge>
                : <Badge variant="warning">Contrato pendente</Badge>}
            </div>

            {/* Abas */}
            <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
              {(['cadastro', 'historico', 'documentos'] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setDetailsTab(tab)}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${detailsTab === tab ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                  {tab === 'cadastro' ? 'Cadastro' : tab === 'historico' ? 'Histórico' : 'Documentos'}
                </button>
              ))}
            </div>

            {/* Aba Cadastro */}
            {detailsTab === 'cadastro' && (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Documento</p>
                    <p className="mt-2 font-semibold text-slate-900 dark:text-white">{formatCpfCnpj(selectedClient.cpf_cnpj)}</p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Status</p>
                    <div className="mt-2"><Badge variant={statusVariant(selectedClient.status)}>{statusLabel(selectedClient.status)}</Badge></div>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">E-mail</p>
                    <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{selectedClient.email || 'Não informado'}</p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Telefone</p>
                    <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{selectedClient.phone ? formatPhone(selectedClient.phone) : 'Não informado'}</p>
                  </div>
                  <div className="rounded-xl border border-brand-100 bg-brand-50/60 p-4 dark:border-brand-900/40 dark:bg-brand-950/30">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-brand-500 dark:text-brand-400">Dia de vencimento</p>
                    <p className="mt-2 text-sm font-semibold text-brand-800 dark:text-brand-200">
                      {selectedClient.billing_day ? `Todo dia ${selectedClient.billing_day}` : 'Não configurado'}
                    </p>
                    <p className="mt-0.5 text-xs text-brand-500/70 dark:text-brand-400/60">Padrão para novos contratos</p>
                  </div>
                </div>
                <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Endereço</p>
                  <p className="mt-2 text-sm leading-6 text-slate-700 dark:text-slate-300">
                    {[selectedClient.address_line, selectedClient.address_number, selectedClient.neighborhood, selectedClient.city, selectedClient.state].filter(Boolean).join(', ') || 'Não informado'}
                  </p>
                </div>
                {(selectedClient.contacts || []).length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Contatos adicionais</p>
                    <div className="space-y-2">
                      {(selectedClient.contacts || []).map((c, i) => (
                        <div key={i} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/50">
                          <p className="font-medium text-slate-900 dark:text-white">{c.name}{c.role && <span className="ml-2 text-xs font-normal text-slate-400">({c.role})</span>}</p>
                          <p className="mt-0.5 text-xs text-slate-500">{[c.phone && formatPhone(c.phone), c.email].filter(Boolean).join(' · ')}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Veículos vinculados</p>
                  {(vehiclesByClient[selectedClient.id] || []).length === 0 ? (
                    <p className="text-sm text-slate-400">Nenhum veículo vinculado.</p>
                  ) : (
                    <div className="space-y-1.5">
                      {(vehiclesByClient[selectedClient.id] || []).map((v) => (
                        <div key={v.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900/50">
                          <span className="font-medium text-slate-900 dark:text-white">{v.plate}</span>
                          <Badge variant={statusVariant(v.status)}>{statusLabel(v.status)}</Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Aba Histórico */}
            {detailsTab === 'historico' && (
              <div>
                <div className="mb-3 flex justify-end">
                  <Button type="button" variant="secondary" onClick={downloadTimelinePdf} className="gap-1.5">
                    <Download className="h-4 w-4" /> Exportar PDF
                  </Button>
                </div>
                {timelineLoading ? (
                  <div className="space-y-3">
                    {[1,2,3].map((i) => <div key={i} className="flex gap-3"><div className="h-7 w-7 shrink-0 animate-pulse rounded-full bg-slate-100 dark:bg-slate-800" /><div className="flex-1 space-y-1.5 pt-1"><div className="h-3 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-800" /><div className="h-3 w-1/2 animate-pulse rounded bg-slate-100 dark:bg-slate-800" /></div></div>)}
                  </div>
                ) : clientTimeline.length === 0 ? (
                  <p className="text-sm text-slate-400">Nenhum evento registrado.</p>
                ) : (
                  <ol className="relative border-l border-slate-200 dark:border-slate-700">
                    {clientTimeline.map((event) => {
                      const cfg = {
                        contract:        { bg: 'bg-brand-100 dark:bg-brand-900/50',   icon: <FileText className="h-3.5 w-3.5 text-brand-600 dark:text-brand-300" /> },
                        os:              { bg: 'bg-slate-100 dark:bg-slate-800',        icon: <Wrench className="h-3.5 w-3.5 text-slate-600 dark:text-slate-300" /> },
                        billing_paid:    { bg: 'bg-emerald-100 dark:bg-emerald-900/50', icon: <CheckCircle className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-300" /> },
                        billing_overdue: { bg: 'bg-rose-100 dark:bg-rose-900/50',       icon: <AlertCircle className="h-3.5 w-3.5 text-rose-600 dark:text-rose-300" /> },
                        billing_pending: { bg: 'bg-amber-100 dark:bg-amber-900/50',     icon: <Clock className="h-3.5 w-3.5 text-amber-600 dark:text-amber-300" /> },
                      }[event.kind];
                      return (
                        <li key={event.key} className="mb-4 ml-5">
                          <span className={`absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full ring-2 ring-white dark:ring-slate-950 ${cfg.bg}`}>{cfg.icon}</span>
                          <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                            <p className="text-xs font-semibold text-slate-900 dark:text-white">{event.title}</p>
                            <p className="mt-0.5 text-xs text-slate-500">{event.subtitle}</p>
                            <time className="mt-1 block text-[10px] text-slate-400">{event.date}</time>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
            )}

            {/* Aba Documentos */}
            {detailsTab === 'documentos' && (
              <div className="space-y-4">
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <Select value={docCategory} onChange={(e) => setDocCategory(e.target.value)} className="w-44">
                      {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </Select>
                    <input type="file" multiple className={fileInputClass} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                    <Button type="button" disabled={uploading || !docFiles.length} onClick={uploadDocuments}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
                  </div>
                )}
                {clientDocuments.length === 0 ? (
                  <EmptyState icon={FileText} title="Nenhum documento" description="Nenhum documento foi anexado a este cliente." />
                ) : (
                  <div className="space-y-3">
                    {clientDocuments.map((doc) => (
                      <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                            <p className="mt-0.5 text-xs text-slate-400">Categoria: {doc.category}</p>
                            {envioMeta(doc) && <p className="mt-0.5 text-xs text-slate-400">{envioMeta(doc)}</p>}
                            {doc.review_notes && <p className="mt-0.5 text-xs text-slate-400">Obs.: {doc.review_notes}</p>}
                          </div>
                          <Badge variant={statusVariant(doc.review_status)}>{statusLabel(doc.review_status)}</Badge>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Visualizar</a>
                          <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Baixar</a>
                          {canEdit && (
                            <>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                              <button type="button" onClick={() => deleteDocument(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={modalOpen}
        onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }}
        title={isEditing ? 'Editar cliente' : 'Novo cliente'}
        description="Preencha os dados principais do cadastro. Você também pode incluir documentação inicial no mesmo fluxo."
        size="xl"
      >
        <form className="space-y-6" onSubmit={submitClient}>
          {modalError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
              {modalError}
            </div>
          )}

          <FormSection title="Dados principais">
            <FormGrid cols={2}>
              <FormField label="Nome / razão social" required>
                <Input placeholder="Nome completo ou razão social" value={form.name} onChange={(e) => handleChange('name', e.target.value)} required />
              </FormField>
              <FormField label="Nome fantasia" hint="Opcional — usado principalmente para pessoa jurídica">
                <Input placeholder="Nome fantasia" value={form.trade_name} onChange={(e) => handleChange('trade_name', e.target.value)} />
              </FormField>
              <FormGrid cols={2}>
                <FormField label="Tipo de pessoa" required>
                  <Select value={form.type} onChange={(e) => handleChange('type', e.target.value)}>
                    <option value="pf">Pessoa física</option>
                    <option value="pj">Pessoa jurídica</option>
                  </Select>
                </FormField>
                <FormField label="Status" required>
                  <Select value={form.status} onChange={(e) => handleChange('status', e.target.value)}>
                    <option value="ativo">Ativo</option>
                    <option value="inativo">Inativo</option>
                    <option value="inadimplente">Inadimplente</option>
                    <option value="suspenso">Suspenso</option>
                  </Select>
                </FormField>
              </FormGrid>
              <FormField label={form.type === 'pj' ? 'CNPJ' : 'CPF'} required>
                <Input placeholder={form.type === 'pj' ? '00.000.000/0001-00' : '000.000.000-00'} value={form.cpf_cnpj} onChange={(e) => handleChange('cpf_cnpj', e.target.value)} required />
              </FormField>
              <FormField label="RG / Inscrição Estadual">
                <Input value={form.rg_ie} onChange={(e) => handleChange('rg_ie', e.target.value)} />
              </FormField>
              <FormField label="Data de nascimento">
                <Input type="date" value={form.birth_date} onChange={(e) => handleChange('birth_date', e.target.value)} />
              </FormField>
              <FormField label="E-mail principal" required>
                <Input type="email" placeholder="email@empresa.com" value={form.email} onChange={(e) => handleChange('email', e.target.value)} required />
              </FormField>
              <FormField label="Telefone principal">
                <div className="flex items-center gap-3">
                  <Input placeholder="(11) 99999-0000" value={form.phone} onChange={(e) => handleChange('phone', e.target.value)} />
                  <label
                    className={[
                      'flex shrink-0 cursor-pointer select-none items-center gap-2 rounded-xl border px-3 py-2.5 text-xs font-bold uppercase tracking-wide transition-colors',
                      form.send_boleto_whatsapp
                        ? 'border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-300'
                        : 'border-orange-300 bg-orange-50 text-orange-600 hover:bg-orange-100 dark:border-orange-800/60 dark:bg-orange-950/20 dark:text-orange-400',
                    ].join(' ')}
                  >
                    <input
                      type="checkbox"
                      checked={form.send_boleto_whatsapp}
                      onChange={(e) => setForm((prev) => ({ ...prev, send_boleto_whatsapp: e.target.checked }))}
                      className="h-4 w-4 rounded accent-emerald-600"
                    />
                    Enviar boleto via Whats
                  </label>
                </div>
              </FormField>
            </FormGrid>
            <FormField label="E-mails adicionais" hint="Um por linha ou separados por vírgula">
              <Textarea placeholder="outro@email.com, terceiro@email.com" value={form.extra_emails} onChange={(e) => handleChange('extra_emails', e.target.value)} className="min-h-[72px]" />
            </FormField>
          </FormSection>

          <FormDivider />

          <FormSection title="Contatos adicionais">
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400 dark:text-slate-500">Responsáveis, técnicos ou gestores adicionais.</p>
              <Button type="button" variant="secondary" onClick={addContact} className="gap-1.5">
                <Plus className="h-3.5 w-3.5" /> Adicionar contato
              </Button>
            </div>
            {form.contacts.map((contact, i) => (
              <div key={i} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/50 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
                <Input placeholder="Nome" value={contact.name} onChange={(e) => updateContact(i, 'name', e.target.value)} />
                <Input placeholder="Telefone" value={contact.phone} onChange={(e) => updateContact(i, 'phone', formatPhone(e.target.value))} />
                <Input placeholder="E-mail" value={contact.email} onChange={(e) => updateContact(i, 'email', e.target.value)} />
                <Input placeholder="Cargo" value={contact.role} onChange={(e) => updateContact(i, 'role', e.target.value)} />
                <button type="button" onClick={() => removeContact(i)} className="flex items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-3 text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </FormSection>

          <FormDivider />

          <FormSection title="Endereço">
            <div className="flex gap-2">
              <FormField label="CEP" className="w-40">
                <Input placeholder="00000-000" value={form.zip_code} onChange={(e) => handleChange('zip_code', e.target.value)} onBlur={(e) => fillAddressFromCep(e.target.value)} />
              </FormField>
              <div className="flex items-end">
                <Button type="button" variant="secondary" onClick={() => fillAddressFromCep(form.zip_code)} disabled={lookingUpCep}>
                  {lookingUpCep ? 'Buscando…' : 'Buscar CEP'}
                </Button>
              </div>
            </div>
            <FormGrid cols={3}>
              <FormField label="Logradouro" className="col-span-2">
                <Input placeholder="Rua / Avenida" value={form.address_line} onChange={(e) => handleChange('address_line', e.target.value)} />
              </FormField>
              <FormField label="Número">
                <Input placeholder="Nº" value={form.address_number} onChange={(e) => handleChange('address_number', e.target.value)} />
              </FormField>
              <FormField label="Complemento">
                <Input placeholder="Apto, sala…" value={form.address_complement} onChange={(e) => handleChange('address_complement', e.target.value)} />
              </FormField>
              <FormField label="Bairro">
                <Input placeholder="Bairro" value={form.neighborhood} onChange={(e) => handleChange('neighborhood', e.target.value)} />
              </FormField>
              <FormField label="Cidade">
                <Input placeholder="Cidade" value={form.city} onChange={(e) => handleChange('city', e.target.value)} />
              </FormField>
              <FormField label="UF">
                <Input placeholder="SP" value={form.state} onChange={(e) => handleChange('state', e.target.value)} maxLength={2} />
              </FormField>
            </FormGrid>
            <FormField
              label="Dia de vencimento preferido"
              hint="1 a 31 — usado como padrão ao gerar contratos e cobranças. Em meses mais curtos, cai no último dia."
              error={erroDiaVencimento(form.billing_day) ?? undefined}
            >
              <BillingDayInput
                value={form.billing_day}
                onChange={(v) => setForm((p) => ({ ...p, billing_day: v }))}
                placeholder="Ex.: 20"
                className="max-w-[120px]"
              />
            </FormField>
            <FormField label="Observações">
              <Textarea placeholder="Anotações administrativas internas" value={form.notes} onChange={(e) => handleChange('notes', e.target.value)} />
            </FormField>
          </FormSection>

          <FormDivider />

          <FormSection title="Contatos de emergência (pessoas autorizadas)">
            <FormGrid cols={3}>
              <FormField label="Contato 1">
                <Input placeholder="Nome" value={form.em1_name} onChange={(e) => handleChange('em1_name', e.target.value)} />
              </FormField>
              <FormField label="Telefone">
                <Input value={form.em1_phone} onChange={(e) => handleChange('em1_phone', e.target.value)} />
              </FormField>
              <FormField label="Celular">
                <Input value={form.em1_mobile} onChange={(e) => handleChange('em1_mobile', e.target.value)} />
              </FormField>
              <FormField label="Contato 2">
                <Input placeholder="Nome" value={form.em2_name} onChange={(e) => handleChange('em2_name', e.target.value)} />
              </FormField>
              <FormField label="Telefone">
                <Input value={form.em2_phone} onChange={(e) => handleChange('em2_phone', e.target.value)} />
              </FormField>
              <FormField label="Celular">
                <Input value={form.em2_mobile} onChange={(e) => handleChange('em2_mobile', e.target.value)} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormDivider />

          <FormSection title="Financeiro">
            <FormGrid cols={3}>
              <FormField label="Formato do Boleto" required>
                <Select value={form.boleto_format} onChange={(e) => handleChange('boleto_format', e.target.value)}>
                  <option value="unico">Boleto Único</option>
                  <option value="individual">Boleto Individual</option>
                </Select>
              </FormField>
              <FormField label="Taxa de Emissão do Boleto">
                <Select value={form.boleto_fee} onChange={(e) => handleChange('boleto_fee', e.target.value)}>
                  <option value="nao">Não</option>
                  <option value="sim">Sim</option>
                </Select>
              </FormField>
              <FormField label="Emitir Nota Fiscal">
                <Select value={form.issue_invoice} onChange={(e) => handleChange('issue_invoice', e.target.value)}>
                  <option value="sim">Sim</option>
                  <option value="nao">Não</option>
                </Select>
              </FormField>
              <FormField label="Tributação">
                <Select value={form.tributacao} onChange={(e) => handleChange('tributacao', e.target.value)}>
                  <option value="dentro_municipio">Dentro do município</option>
                  <option value="fora_municipio">Fora do município</option>
                  <option value="isento">Isento</option>
                </Select>
              </FormField>
              <FormField label="Reter ISS">
                <Select value={form.iss_retido} onChange={(e) => handleChange('iss_retido', e.target.value)}>
                  <option value="nao">Não</option>
                  <option value="sim">Sim</option>
                </Select>
              </FormField>
              <FormField label="Optante do Simples Nacional">
                <Select value={form.optante_simples} onChange={(e) => handleChange('optante_simples', e.target.value)}>
                  <option value="sim">Sim</option>
                  <option value="nao">Não</option>
                </Select>
              </FormField>
              <FormField label="Tipo de Envio">
                <Select value={form.delivery_method} onChange={(e) => handleChange('delivery_method', e.target.value)}>
                  <option value="email">Email</option>
                  <option value="whatsapp">WhatsApp</option>
                  <option value="todos">Todos</option>
                </Select>
              </FormField>
            </FormGrid>
          </FormSection>

          <FormDivider />

          <FormSection title="Documentação inicial (opcional)">
            <p className="text-xs text-slate-400 dark:text-slate-500">Arquivos enviados automaticamente após salvar o cliente.</p>
            <FormGrid cols={2}>
              <FormField label="Categoria">
                <Select value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
                  {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              </FormField>
              <FormField label="Arquivo(s)">
                <input type="file" multiple className={fileInputClass} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
              </FormField>
            </FormGrid>
          </FormSection>

          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button type="button" variant="secondary" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</Button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando…' : isEditing ? 'Atualizar cliente' : 'Cadastrar cliente'}</Button>
          </div>
        </form>
      </Modal>

      {/* ══ Modal: Veículos vinculados ao cliente ══════════════════════════ */}
      <Modal
        open={vehiclesModalOpen}
        onClose={() => { setVehiclesModalOpen(false); setVehiclesModalClient(null); setVehiclesDetailed([]); }}
        title={vehiclesModalClient ? `Veículos vinculados ao cliente — ${vehiclesModalClient.name}` : 'Veículos vinculados ao cliente'}
        size="2xl"
      >
        {vehiclesModalLoading ? (
          <TableSkeleton rows={5} cols={7} />
        ) : vehiclesDetailed.length === 0 ? (
          <EmptyState icon={Car} title="Nenhum veículo vinculado" description="Este cliente não possui veículos cadastrados." />
        ) : (
          <Table>
            <TableHead>
              <Th>Tipo</Th>
              <Th>Placa</Th>
              <Th>Marca</Th>
              <Th>Modelo</Th>
              <Th>Situação</Th>
              <Th>Tipo Equip.</Th>
              <Th>Modelo Equip.</Th>
              <Th>IMEI</Th>
            </TableHead>
            <TableBody>
              {vehiclesDetailed.map((v) => (
                <Tr key={v.id}>
                  <Td className="text-xs capitalize">{v.type ?? '—'}</Td>
                  <Td className="font-mono font-semibold">{v.plate}</Td>
                  <Td className="text-sm">{v.brand ?? '—'}</Td>
                  <Td className="text-sm">{v.model ?? '—'}</Td>
                  <Td><Badge variant={statusVariant(v.status)}>{statusLabel(v.status)}</Badge></Td>
                  <Td className="text-xs text-slate-500">{v.tracker_plan ?? (v.tracker_imei ? 'BÁSICO' : '—')}</Td>
                  <Td className="text-xs text-slate-500">{v.tracker_model ? `${v.tracker_brand ?? ''} ${v.tracker_model}`.trim() : '—'}</Td>
                  <Td className="font-mono text-xs">{v.tracker_imei ?? '—'}</Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-400">
          Mostrando {vehiclesDetailed.length} registro(s)
        </p>
      </Modal>

      {/* ══ Modal: Veículos onde o cliente é interveniente financeiro ═════ */}
      <Modal
        open={intervModalOpen}
        onClose={() => { setIntervModalOpen(false); setIntervModalClient(null); setIntervContracts([]); }}
        title={intervModalClient ? `Interveniente financeiro — ${intervModalClient.name}` : 'Interveniente financeiro'}
        size="2xl"
      >
        {intervLoading ? (
          <TableSkeleton rows={4} cols={5} />
        ) : intervContracts.length === 0 ? (
          <EmptyState
            icon={Coins}
            title="Nenhum vínculo como interveniente"
            description="Este cliente não responde pela cobrança de contratos de outros clientes."
          />
        ) : (
          <Table>
            <TableHead>
              <Th>Contrato</Th>
              <Th>Placa</Th>
              <Th>Cliente titular</Th>
              <Th>Plano</Th>
              <Th>Situação</Th>
            </TableHead>
            <TableBody>
              {intervContracts.map((c) => (
                <Tr key={c.id}>
                  <Td className="text-xs text-slate-500">#{c.id}</Td>
                  <Td className="font-mono font-semibold">{c.vehicle_plate ?? '—'}</Td>
                  <Td className="text-sm">{c.client_name ?? '—'}</Td>
                  <Td className="text-sm">{c.plan_name ?? '—'}</Td>
                  <Td><Badge variant={statusVariant(c.status)}>{statusLabel(c.status)}</Badge></Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-400">
          Mostrando {intervContracts.length} registro(s)
        </p>
      </Modal>

      {/* ══ Modal: Notas fiscais do cliente (patinha) ══════════════════════ */}
      <Modal
        open={nfseModalOpen}
        onClose={() => { setNfseModalOpen(false); setNfseModalClient(null); setClientNotas([]); }}
        title={nfseModalClient ? `Notas fiscais — ${nfseModalClient.name}` : 'Notas fiscais'}
        size="2xl"
      >
        {nfseLoading ? (
          <TableSkeleton rows={4} cols={6} />
        ) : clientNotas.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="Nenhuma nota fiscal"
            description="Este cliente ainda não possui NFS-e emitida. As notas são geradas a partir das cobranças no fechamento."
          />
        ) : (
          <Table>
            <TableHead>
              <Th>Nº NFS-e</Th>
              <Th>Cobrança</Th>
              <Th>Valor</Th>
              <Th>Emissão</Th>
              <Th>Situação</Th>
              <Th className="w-24" />
            </TableHead>
            <TableBody>
              {clientNotas.map((n) => (
                <Tr key={n.billing_id}>
                  <Td className="font-mono font-semibold">{n.numero_nfse ?? '—'}</Td>
                  <Td className="text-xs">#{n.billing_id}{n.titulo ? ` · ${n.titulo}` : ''}</Td>
                  <Td className="font-mono">
                    {n.valor != null ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n.valor) : '—'}
                  </Td>
                  <Td className="text-xs">{n.data_emissao ? new Date(n.data_emissao).toLocaleDateString('pt-BR') : '—'}</Td>
                  <Td>
                    <Badge variant={n.status === 'emitida' ? 'success' : n.status === 'erro' ? 'danger' : 'warning'}>
                      {n.status === 'emitida' ? 'Emitida' : n.status === 'erro' ? 'Erro' : 'Processando'}
                    </Badge>
                  </Td>
                  <Td>
                    {/* O PDF vem primeiro: mandar o operador para a consulta do
                        governo para depois baixar de la era um desvio inutil. */}
                    <div className="flex items-center gap-1.5">
                      {n.status === 'emitida' && (
                        <button
                          type="button"
                          onClick={() => abrirNotaPdf(n.billing_id)}
                          className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 text-[11px] font-semibold text-brand-700 transition hover:bg-brand-100 dark:border-brand-900/40 dark:bg-brand-950/30 dark:text-brand-400"
                        >
                          Ver PDF
                        </button>
                      )}
                      {n.link_visualizacao && (
                        <a
                          href={n.link_visualizacao}
                          target="_blank"
                          rel="noreferrer"
                          title="Consulta publica no portal da NFS-e"
                          className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                        >
                          Consulta
                        </a>
                      )}
                    </div>
                  </Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-400">
          Mostrando {clientNotas.length} registro(s)
        </p>
      </Modal>

      {/* ══ Modal: Ficha de adesão / contratos do cliente ══════════════════ */}
      <Modal
        open={contractSheetOpen}
        onClose={() => { setContractSheetOpen(false); setContractSheetClient(null); setContractDocs([]); setContractSheetItems([]); setContractSignAlvo(''); setContractFile(null); setContractCheck(null); }}
        title={contractSheetClient ? `Contratos — ${contractSheetClient.name}` : 'Contratos'}
        size="2xl"
      >
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Gere o modelo em branco em <strong>Financeiro → Gerar contrato</strong>. Quando o cliente devolver assinado, anexe aqui.
          </p>
          {contractSheetClient && (
            contractSheetClient.contrato_armazenado
              ? <Badge variant="success">Contrato armazenado</Badge>
              : <Badge variant="warning">Assinado pendente</Badge>
          )}
        </div>

        {/* Contratos vinculados (registros): visualizar e cancelar/excluir. */}
        <div className="mb-4">
          <p className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">Contratos vinculados ({contractSheetItems.length})</p>
          {contractSheetLoading ? (
            <p className="text-xs text-slate-400">Carregando…</p>
          ) : contractSheetItems.length === 0 ? (
            <p className="text-xs text-slate-400">Nenhum contrato vinculado a este cliente.</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wider text-slate-500 dark:border-slate-700 dark:bg-slate-800/50">
                    <th className="px-3 py-2 font-semibold">Plano</th>
                    <th className="px-3 py-2 font-semibold">Vínculo</th>
                    <th className="px-3 py-2 font-semibold">Vigência</th>
                    <th className="px-3 py-2 font-semibold">Situação</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {contractSheetItems.map((c) => {
                    const sit = contractSituacao(c);
                    return (
                      <tr key={c.id} className="hover:bg-slate-50/70 dark:hover:bg-slate-800/40">
                        <td className="px-3 py-2"><span className="text-xs text-slate-400">#{c.id}</span> {c.plan_name ?? '—'}</td>
                        <td className="px-3 py-2 text-xs text-slate-500">{c.vehicle_plate || c.tracker_identifier || 'Geral'}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs">
                          {c.start_date ? new Date(c.start_date + 'T12:00:00').toLocaleDateString('pt-BR') : '—'}
                          {' → '}
                          {c.end_date ? new Date(c.end_date + 'T12:00:00').toLocaleDateString('pt-BR') : 'Indeterminada'}
                        </td>
                        <td className="px-3 py-2"><Badge variant={sit.variant}>{sit.label}</Badge></td>
                        <td className="px-3 py-2">
                          <div className="flex items-center justify-end gap-1.5">
                            <button type="button" onClick={() => baixarContrato(c.id)} className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-100 dark:border-brand-900/40 dark:bg-brand-950/30 dark:text-brand-400">Ver</button>
                            {canEdit && (
                              <button type="button" onClick={() => excluirContrato(c.id)} className="rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-900/40 dark:text-rose-400 dark:hover:bg-rose-950/30">Excluir</button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Contrato assinado: sobe aqui mesmo, já na categoria certa. */}
        <div className="mb-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Contrato assinado</p>
            <span className="text-xs text-slate-400">{contractDocs.length} arquivo(s)</span>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">Depois que o cliente devolver o contrato assinado, anexe o arquivo (PDF ou imagem) aqui. O sistema confere se ele foi preenchido antes de guardar.</p>

          {canEdit && contractSheetItems.some(c => c.status !== 'cancelado' && c.status !== 'encerrado') && (
            <div className="mt-3">
              <p className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">Ao enviar, colocar &quot;em vigor&quot; o contrato:</p>
              <Select value={contractSignAlvo} onChange={(e) => setContractSignAlvo(e.target.value)} className="w-full">
                <option value="">Não vincular — só guardar o arquivo</option>
                {contractSheetItems.filter(c => c.status !== 'cancelado' && c.status !== 'encerrado').map(c => (
                  <option key={c.id} value={c.id}>#{c.id} • {c.plan_name || 'Plano'}{c.vehicle_plate ? ` • ${c.vehicle_plate}` : ''}{c.signed ? ' — já assinado' : ''}</option>
                ))}
              </Select>
            </div>
          )}

          {canEdit && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                type="file"
                accept="application/pdf,image/*"
                className={fileInputClass}
                onChange={(e) => { setContractFile(e.target.files?.[0] ?? null); setContractCheck(null); }}
              />
              <Button type="button" disabled={uploadingContract || !contractFile} onClick={uploadSignedContract}>
                {uploadingContract ? 'Conferindo…' : 'Enviar contrato assinado'}
              </Button>
            </div>
          )}

          {contractCheck && (
            <p className={[
              'mt-3 rounded-xl border px-3 py-2 text-xs',
              contractCheck.level === 'ok'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400'
                : 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400',
            ].join(' ')}>
              {contractCheck.message}
            </p>
          )}

          {contractSheetLoading ? (
            <p className="mt-3 text-xs text-slate-400">Carregando…</p>
          ) : contractDocs.length === 0 ? (
            <p className="mt-3 text-xs text-slate-400">Nenhum contrato assinado enviado ainda.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {contractDocs.map((doc) => (
                <li key={doc.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
                  <span className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                    <span className="flex flex-col">
                      <span>{doc.file_name}</span>
                      {envioMeta(doc) && <span className="text-[11px] text-slate-400">{envioMeta(doc)}</span>}
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Visualizar</a>
                    <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Baixar</a>
                    {canEdit && (
                      <button type="button" onClick={() => removeContractDoc(doc.id)} className="rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-900/40 dark:text-rose-400 dark:hover:bg-rose-950/30">Excluir</button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Modal>

      {/* ══ Modal: Boletos do cliente ══════════════════════════════════════ */}
      <Modal
        open={billingsModalOpen}
        onClose={() => { setBillingsModalOpen(false); setBillingsModalClient(null); setClientBillings([]); setCarnes([]); setBillingSummaryExpanded(false); setSelectedBillingIds([]); }}
        title={billingsModalClient ? `Boletos do cliente — ${billingsModalClient.name}` : 'Boletos do cliente'}
        size="2xl"
      >
        {/* Resumo financeiro */}
        <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50">
          <div className="flex items-center justify-between px-4 py-3">
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">Resumo financeiro</p>
            <button
              type="button"
              onClick={() => setBillingSummaryExpanded((p) => !p)}
              className="rounded-lg bg-purple-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-purple-700"
            >
              {billingSummaryExpanded ? 'Ocultar' : 'Exibir'}
            </button>
          </div>
          {billingSummaryExpanded && !billingsLoading && (
            <div className="grid gap-3 px-4 pb-4 sm:grid-cols-3">
              {[
                { label: 'Total cobrado', value: clientBillings.reduce((s, b) => s + b.amount, 0) },
                { label: 'Total pago', value: clientBillings.reduce((s, b) => s + (b.paid_amount ?? 0), 0) },
                { label: 'Pendente / vencido', value: clientBillings.filter((b) => ['pendente', 'vencida'].includes(b.status)).reduce((s, b) => s + b.amount, 0) },
              ].map(({ label, value }) => (
                <div key={label} className="rounded-lg border border-slate-200 bg-white p-3 text-center dark:border-slate-700 dark:bg-slate-800">
                  <p className="text-xs text-slate-400">{label}</p>
                  <p className="mt-1 text-base font-bold text-slate-900 dark:text-white">
                    {new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Soma dos boletos selecionados (pagamento em lote) */}
        {selectedBillingIds.length > 0 && (() => {
          const fmt = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
          const sel = clientBillings.filter((b) => selectedBillingIds.includes(b.id));
          const total = sel.reduce((s, b) => s + b.amount, 0);
          const totalJuros = sel.reduce((s, b) => s + (valorComJuros(b) ?? b.amount), 0);
          return (
            <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-brand-300 bg-brand-50 px-4 py-3 text-sm dark:border-brand-700 dark:bg-brand-950/30">
              <span className="font-bold text-brand-800 dark:text-brand-200">
                {sel.length} boleto(s) selecionado(s)
              </span>
              <span className="text-slate-600 dark:text-slate-300">
                Total sem juros: <strong className="font-mono text-slate-900 dark:text-white">{fmt(total)}</strong>
              </span>
              <span className="text-slate-600 dark:text-slate-300">
                Total com juros: <strong className="font-mono text-rose-600 dark:text-rose-400">{fmt(totalJuros)}</strong>
              </span>
              {sel.length >= 2 && (
                <Button onClick={openUnifyModal} className="!py-1.5 text-xs">
                  Unificar em 1 boleto
                </Button>
              )}
              {sel.length >= 2 && (
                <Button variant="secondary" onClick={gerarCarne} disabled={gerandoCarne} className="!py-1.5 text-xs">
                  {gerandoCarne ? 'Gerando carnê…' : 'Gerar carnê'}
                </Button>
              )}
              <button
                type="button"
                onClick={() => setSelectedBillingIds([])}
                className="ml-auto text-xs text-slate-400 underline hover:text-slate-600 dark:hover:text-slate-200"
              >
                Limpar seleção
              </button>
            </div>
          );
        })()}

        {/* Carnês já gerados deste cliente — reabrir/baixar */}
        {carnes.length > 0 && (
          <div className="mb-4 rounded-xl border border-slate-200 dark:border-slate-700">
            <p className="border-b border-slate-100 px-4 py-2 text-xs font-semibold text-slate-500 dark:border-slate-800 dark:text-slate-400">
              Carnês gerados
            </p>
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {carnes.map((c) => {
                const fmt = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
                const prontas = c.parcelas_registradas >= c.parcelas;
                return (
                  <div key={c.lote_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-sm">
                    <span className="font-semibold text-slate-700 dark:text-slate-200">Carnê #{c.lote_id}</span>
                    <span className="text-slate-500 dark:text-slate-400">{c.parcelas} parcela(s) · {fmt(c.total)}</span>
                    {c.criado_em && <span className="text-xs text-slate-400">{new Date(c.criado_em).toLocaleDateString('pt-BR')}</span>}
                    {!prontas && (
                      <Badge variant="warning">{c.parcelas_registradas}/{c.parcelas} prontas</Badge>
                    )}
                    <Button variant="secondary" onClick={() => baixarCarne(c.lote_id)} className="ml-auto !py-1.5 text-xs">
                      Baixar carnê
                    </Button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {billingsLoading ? (
          <TableSkeleton rows={5} cols={8} />
        ) : clientBillings.length === 0 ? (
          <EmptyState icon={DollarSign} title="Nenhuma cobrança encontrada" description="Não há boletos registrados para este cliente." />
        ) : (
          <Table>
            <TableHead>
              <Th className="w-8">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded accent-brand-700"
                  title="Selecionar todos os boletos em aberto"
                  checked={
                    clientBillings.some((b) => b.status === 'pendente' || b.status === 'vencida') &&
                    clientBillings.filter((b) => b.status === 'pendente' || b.status === 'vencida').every((b) => selectedBillingIds.includes(b.id))
                  }
                  onChange={(e) => setSelectedBillingIds(
                    e.target.checked
                      ? clientBillings.filter((b) => b.status === 'pendente' || b.status === 'vencida').map((b) => b.id)
                      : []
                  )}
                />
              </Th>
              <Th>Nº</Th>
              <Th>Tipo</Th>
              <Th>Emissão</Th>
              <Th>Vencimento</Th>
              <Th>Pagamento</Th>
              <Th>Valor</Th>
              <Th>Valor c/ Juros</Th>
              <Th>Valor Pago</Th>
              <Th>Parcela</Th>
              <Th>Mês Ref.</Th>
              <Th>Situação</Th>
              <Th className="w-44" />
            </TableHead>
            <TableBody>
              {clientBillings.map((b) => {
                const fmt = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v);
                const isAberto = b.status === 'pendente' || b.status === 'vencida';
                const juros = valorComJuros(b);
                return (
                  <Tr key={b.id}>
                    <Td>
                      {isAberto ? (
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded accent-brand-700"
                          checked={selectedBillingIds.includes(b.id)}
                          onChange={() => setSelectedBillingIds((prev) =>
                            prev.includes(b.id) ? prev.filter((id) => id !== b.id) : [...prev, b.id]
                          )}
                        />
                      ) : null}
                    </Td>
                    <Td className="text-xs text-slate-500">{b.id}</Td>
                    <Td className="text-xs capitalize">{b.billing_type === 'prorata' ? 'Pró-rata' : b.billing_type === 'recorrente' ? 'Mensalidade' : b.billing_type}</Td>
                    <Td className="text-xs">{b.created_at ? new Date(b.created_at).toLocaleDateString('pt-BR') : '—'}</Td>
                    <Td className="text-sm font-medium">{b.due_date}</Td>
                    <Td className="text-xs">{b.payment_date ?? '—'}</Td>
                    <Td className="font-mono font-semibold">{fmt(b.amount)}</Td>
                    <Td className="font-mono font-semibold text-rose-600 dark:text-rose-400">
                      {juros != null ? fmt(juros) : '—'}
                    </Td>
                    <Td className="font-mono text-emerald-700 dark:text-emerald-400">{fmt(b.paid_amount ?? 0)}</Td>
                    <Td className="text-xs text-center">
                      {b.installment_number ? `${b.installment_number}/${b.installment_total}` : '1/1'}
                    </Td>
                    <Td className="text-xs">{b.period_label ?? '—'}</Td>
                    <Td><Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge></Td>
                    <Td>
                      <div className="flex justify-end gap-1">
                        <ActionBtn color="purple" icon={Wrench} title="Alterar boleto" onClick={() => openEditBilling(b)} />
                        <ActionBtn color="purple" icon={Flag} title="Histórico de operações" onClick={() => openBillingHistory(b)} />
                        {isAberto && (
                          <>
                            <ActionBtn color="blue" icon={Mail} title="Enviar boleto por e-mail" onClick={() => sendBoletoEmail(b)} />
                            <ActionBtn color="green" icon={MessageCircle} title="Enviar boleto via Whats" onClick={() => sendBoletoWhats(b)} />
                            {b.boleto_ailos && (
                              <ActionBtn color="teal" icon={Download} title="Baixar boleto PDF" onClick={() => baixarBoletoPdf(b)} />
                            )}
                          </>
                        )}
                        {b.status === 'paga' && (
                          <ActionBtn color="blue" icon={Receipt} title="Emitir comprovante de pagamento" onClick={() => baixarComprovante(b)} />
                        )}
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </TableBody>
          </Table>
        )}
        <p className="mt-3 text-xs text-slate-400">
          Mostrando {clientBillings.length} registro(s)
        </p>
      </Modal>

      {/* ══ Modal: Unificar boletos em um único (negociação) ═══════════════ */}
      <Modal
        open={unifyOpen}
        onClose={() => setUnifyOpen(false)}
        title={`Unificar ${selectedBillingIds.length} boletos em um único`}
        subtitle="As cobranças originais são canceladas e substituídas por um boleto avulso"
        size="md"
      >
        <div className="space-y-4">
          <FormGrid>
            <FormField label="Vencimento do boleto único" required>
              <Input
                type="date"
                value={unifyForm.due_date}
                onChange={(e) => setUnifyForm((p) => ({ ...p, due_date: e.target.value }))}
              />
            </FormField>
            <FormField label="Valor (R$)" hint="Pré-preenchido com a soma — ajuste se houve negociação">
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={unifyForm.amount}
                onChange={(e) => setUnifyForm((p) => ({ ...p, amount: e.target.value }))}
              />
            </FormField>
          </FormGrid>
          <FormField label="Observações (opcional)">
            <Textarea
              placeholder="Ex.: negociação com o cliente em 14/07…"
              value={unifyForm.notes}
              onChange={(e) => setUnifyForm((p) => ({ ...p, notes: e.target.value }))}
              className="min-h-[64px]"
            />
          </FormField>
          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="secondary" onClick={() => setUnifyOpen(false)}>Cancelar</Button>
            <Button onClick={saveUnify} disabled={unifying || !unifyForm.due_date}>
              {unifying ? 'Unificando…' : 'Criar boleto único'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* ══ Modal: Alterar boleto (valor/vencimento com justificativa) ═════ */}
      <Modal
        open={!!editBilling}
        onClose={() => setEditBilling(null)}
        title={editBilling ? `Alterar boleto #${editBilling.id}` : 'Alterar boleto'}
        subtitle="Alterações de valor e vencimento ficam registradas no histórico"
        size="md"
      >
        <div className="space-y-4">
          <FormGrid>
            <FormField label="Valor (R$)" required>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={editBillingForm.amount}
                onChange={(e) => setEditBillingForm((p) => ({ ...p, amount: e.target.value }))}
              />
            </FormField>
            <FormField label="Vencimento" required>
              <Input
                type="date"
                value={editBillingForm.due_date}
                onChange={(e) => setEditBillingForm((p) => ({ ...p, due_date: e.target.value }))}
              />
            </FormField>
          </FormGrid>
          <FormField label="Justificativa" required hint="Obrigatória — fica gravada no histórico de operações">
            <Textarea
              placeholder="Ex.: negociação com o cliente, correção de valor…"
              value={editBillingForm.justification}
              onChange={(e) => setEditBillingForm((p) => ({ ...p, justification: e.target.value }))}
              className="min-h-[72px]"
            />
          </FormField>
          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button variant="secondary" onClick={() => setEditBilling(null)}>Cancelar</Button>
            <Button onClick={saveEditBilling} disabled={savingBilling}>
              {savingBilling ? 'Salvando…' : 'Salvar alteração'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* ══ Modal: Histórico de operações do boleto ════════════════════════ */}
      <Modal
        open={!!historyBilling}
        onClose={() => { setHistoryBilling(null); setBillingChanges([]); }}
        title={historyBilling ? `Histórico de operações — boleto #${historyBilling.id}` : 'Histórico'}
        size="xl"
      >
        {historyLoading ? (
          <TableSkeleton rows={3} cols={5} />
        ) : billingChanges.length === 0 ? (
          <EmptyState
            icon={Flag}
            title="Sem alterações registradas"
            description="Este boleto não teve valor ou vencimento alterados."
          />
        ) : (
          <Table>
            <TableHead>
              <Th>Data</Th>
              <Th>Campo</Th>
              <Th>De</Th>
              <Th>Para</Th>
              <Th>Justificativa</Th>
            </TableHead>
            <TableBody>
              {billingChanges.map((ch) => (
                <Tr key={ch.id}>
                  <Td className="text-xs">{ch.created_at ? new Date(ch.created_at).toLocaleString('pt-BR') : '—'}</Td>
                  <Td className="text-xs font-medium">
                    {ch.field_name === 'amount' ? 'Valor' : ch.field_name === 'due_date' ? 'Vencimento' : ch.field_name}
                  </Td>
                  <Td className="font-mono text-xs">{ch.previous_value ?? '—'}</Td>
                  <Td className="font-mono text-xs">{ch.new_value ?? '—'}</Td>
                  <Td className="text-xs">{ch.justification}</Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
      </Modal>
    </PageShell>
  );
}
