'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { SectionHeader } from '@/components/ui/section-header';
import { StatCard } from '@/components/ui/stat-card';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import type {
  OrderType,
  OrderStatus,
  DocumentReviewStatus as ReviewStatus,
  ClientOption,
  VehicleOption,
  TrackerOption,
  UserOption,
} from '@/lib/domain-types';

type ServiceOrder = {
  id: number;
  number: string;
  type: OrderType;
  status: OrderStatus;
  client_id: number;
  vehicle_id?: number | null;
  tracker_id?: number | null;
  technician_id?: number | null;
  scheduled_at?: string | null;
  executed_at?: string | null;
  checklist?: { items?: string[] } | null;
  observations?: string | null;
  client_name?: string | null;
  vehicle_plate?: string | null;
  tracker_label?: string | null;
  technician_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

type OrderLog = {
  id: number;
  previous_status?: OrderStatus | null;
  new_status: OrderStatus;
  notes?: string | null;
  changed_by_id?: number | null;
  changed_by_name?: string | null;
  created_at?: string | null;
};

type OrderDocument = {
  id: number;
  file_name: string;
  category: string;
  review_status: ReviewStatus;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

type OrderFormState = {
  type: OrderType;
  status: OrderStatus;
  client_id: string;
  vehicle_id: string;
  tracker_id: string;
  technician_id: string;
  scheduled_at: string;
  executed_at: string;
  observations: string;
  checklistItems: string[];
};

const initialForm: OrderFormState = {
  type: 'instalacao',
  status: 'aberta',
  client_id: '',
  vehicle_id: '',
  tracker_id: '',
  technician_id: '',
  scheduled_at: '',
  executed_at: '',
  observations: '',
  checklistItems: [''],
};

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';
const areaClass = `${fieldClass} min-h-[88px] resize-y`;

const orderTypeOptions: { value: OrderType; label: string }[] = [
  { value: 'instalacao', label: 'Instalação' },
  { value: 'manutencao', label: 'Manutenção / troca' },
  { value: 'retirada', label: 'Retirada' },
  { value: 'visita_tecnica', label: 'Visita técnica / suporte' },
];

const statusOptions: { value: OrderStatus; label: string }[] = [
  { value: 'aberta', label: 'Aberta' },
  { value: 'em_andamento', label: 'Em andamento' },
  { value: 'concluida', label: 'Concluída' },
  { value: 'cancelada', label: 'Cancelada' },
];

const checklistTemplates: Record<OrderType, string[]> = {
  instalacao: ['Confirmar cliente e veículo', 'Conferir posição e local do equipamento', 'Testar comunicação', 'Registrar fotos da instalação'],
  manutencao: ['Validar causa da manutenção', 'Conferir chicote e alimentação', 'Executar teste funcional', 'Registrar evidência pós-serviço'],
  retirada: ['Confirmar autorização de retirada', 'Retirar equipamento com segurança', 'Registrar estado do equipamento', 'Atualizar devolução ao estoque'],
  visita_tecnica: ['Validar solicitação', 'Executar atendimento em campo', 'Registrar evidências', 'Formalizar conclusão'],
};

const pdfKinds = [
  { value: 'ordem_servico', label: 'Gerar PDF da OS' },
  { value: 'termo_instalacao', label: 'Gerar termo de instalação' },
  { value: 'termo_retirada', label: 'Gerar termo de retirada' },
  { value: 'historico_execucao', label: 'Gerar histórico de execução' },
] as const;

const documentCategoryOptions = ['evidencia_fotografica', 'termo_instalacao', 'termo_retirada', 'anexo_tecnico', 'outro'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function formatDateTimeLabel(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pt-BR');
}

function toLocalDatetimeInput(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const tzOffset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - tzOffset).toISOString().slice(0, 16);
}

function typeLabel(value: OrderType) {
  return orderTypeOptions.find((item) => item.value === value)?.label || value;
}

function OSTable({
  orders,
  loading,
  error,
  canEdit,
  onDetails,
  onEdit,
  statusOptions,
  orderTypeOptions,
}: {
  orders: ServiceOrder[];
  loading: boolean;
  error?: string;
  canEdit: boolean;
  onDetails: (o: ServiceOrder) => void;
  onEdit: (o: ServiceOrder) => void;
  statusOptions: { value: string; label: string }[];
  orderTypeOptions: { value: string; label: string }[];
}) {
  const pg = usePagination(orders, 20);

  if (loading) return <TableSkeleton rows={8} cols={5} />;
  if (error) return <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar as ordens" description="Veja o erro acima e tente novamente." />;
  if (orders.length === 0) return <EmptyState title="Nenhuma ordem encontrada" description="Ajuste os filtros ou abra uma nova OS." />;

  return (
    <>
      <Table>
        <TableHead>
          <Th>Número</Th>
          <Th>Tipo</Th>
          <Th>Cliente / Veículo</Th>
          <Th>Técnico</Th>
          <Th>Status</Th>
          <Th className="w-36" />
        </TableHead>
        <TableBody>
          {pg.slice.map((order) => (
            <Tr key={order.id}>
              <Td className="font-mono font-semibold">{order.number}</Td>
              <Td><span className="text-sm">{orderTypeOptions.find((x) => x.value === order.type)?.label ?? order.type}</span></Td>
              <Td>
                <p className="text-sm">{order.client_name ?? '—'}</p>
                <p className="text-xs text-slate-400">{order.vehicle_plate ?? ''}</p>
              </Td>
              <Td className="text-sm">{order.technician_name ?? '—'}</Td>
              <Td><Badge variant={statusVariant(order.status)}>{statusOptions.find((x) => x.value === order.status)?.label ?? order.status}</Badge></Td>
              <Td>
                <div className="flex justify-end gap-1.5">
                  <Button variant="secondary" onClick={() => onDetails(order)} className="px-3 py-1.5 text-xs">Detalhes</Button>
                  {canEdit && <Button variant="secondary" onClick={() => onEdit(order)} className="px-3 py-1.5 text-xs">Editar</Button>}
                </div>
              </Td>
            </Tr>
          ))}
        </TableBody>
      </Table>
      <Pagination {...pg} onPage={pg.setPage} className="mt-2" />
    </>
  );
}

export default function ServiceOrdersPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/ordens-servico'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [orders, setOrders] = useState<ServiceOrder[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [trackers, setTrackers] = useState<TrackerOption[]>([]);
  const [technicians, setTechnicians] = useState<UserOption[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<ServiceOrder | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<'detalhes' | 'documentos' | 'historico'>('detalhes');
  const [logs, setLogs] = useState<OrderLog[]>([]);
  const [documents, setDocuments] = useState<OrderDocument[]>([]);
  const [form, setForm] = useState<OrderFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [vehicleFilter, setVehicleFilter] = useState('');
  const [technicianFilter, setTechnicianFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [docCategory, setDocCategory] = useState('evidencia_fotografica');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');

  const filteredVehicles = useMemo(() => {
    if (!form.client_id) return vehicles;
    return vehicles.filter((vehicle) => vehicle.client_id === Number(form.client_id));
  }, [vehicles, form.client_id]);

  const filteredTrackers = useMemo(() => {
    if (!form.vehicle_id) return trackers;
    return trackers.filter((tracker) => tracker.vehicle_id === Number(form.vehicle_id));
  }, [trackers, form.vehicle_id]);

  const stats = useMemo(() => ({
    total: orders.length,
    open: orders.filter((item) => item.status === 'aberta').length,
    inProgress: orders.filter((item) => item.status === 'em_andamento').length,
    completed: orders.filter((item) => item.status === 'concluida').length,
  }), [orders]);

  async function loadBaseData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (typeFilter) query.set('type', typeFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      if (vehicleFilter) query.set('vehicle_id', vehicleFilter);
      if (technicianFilter) query.set('technician_id', technicianFilter);
      query.set('limit', '200');

      const [orderResult, clientResult, vehicleResult, trackerResult, userResult] = await Promise.allSettled([
        apiFetch<ServiceOrder[]>(`/service-orders?${query.toString()}`, {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=200', {}, currentToken),
        apiFetch<VehicleOption[]>('/vehicles?limit=500', {}, currentToken),
        apiFetch<TrackerOption[]>('/trackers?limit=200', {}, currentToken),
        apiFetch<UserOption[]>('/users', {}, currentToken),
      ]);

      const orderResponse = orderResult.status === 'fulfilled' ? orderResult.value : [];
      const clientResponse = clientResult.status === 'fulfilled' ? clientResult.value : [];
      const vehicleResponse = vehicleResult.status === 'fulfilled' ? vehicleResult.value : [];
      const trackerResponse = trackerResult.status === 'fulfilled' ? trackerResult.value : [];
      const userResponse = userResult.status === 'fulfilled' ? userResult.value : [];

      if (orderResult.status === 'rejected') {
        throw orderResult.reason;
      }

      setOrders(orderResponse);
      setClients(clientResponse);
      setVehicles(vehicleResponse);
      setTrackers(trackerResponse);
      setTechnicians(userResponse.filter((item) => item.role === 'admin' || item.role === 'operacional'));
      if (selectedOrder) {
        const refreshed = orderResponse.find((item) => item.id === selectedOrder.id) || null;
        setSelectedOrder(refreshed);
      }
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadOrderDetails(currentToken: string, orderId: number) {
    try {
      const [logResponse, documentResponse] = await Promise.all([
        apiFetch<OrderLog[]>(`/service-orders/${orderId}/logs`, {}, currentToken),
        apiFetch<OrderDocument[]>(`/service-orders/${orderId}/documents`, {}, currentToken),
      ]);
      setLogs(logResponse);
      setDocuments(documentResponse);
    } catch (err) {
      setError(parseError(err));
    }
  }

  useEffect(() => {
    if (!token) return;
    loadBaseData(token);
  }, [token]);

  // Busca/filtros dinâmicos (sem precisar clicar em "Filtrar")
  const searchDebounced = useDebouncedValue(search);
  useEffectSkipFirst(() => {
    if (token) loadBaseData(token);
  }, [searchDebounced, statusFilter, typeFilter, clientFilter, vehicleFilter, technicianFilter]);

  useEffect(() => {
    if (!token || !selectedOrder) {
      setLogs([]);
      setDocuments([]);
      return;
    }
    loadOrderDetails(token, selectedOrder.id);
  }, [token, selectedOrder?.id]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
    setDocFiles([]);
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function fillForm(order: ServiceOrder) {
    setSelectedOrder(order);
    setIsEditing(true);
    setModalError('');
    setForm({
      type: order.type,
      status: order.status,
      client_id: order.client_id ? String(order.client_id) : '',
      vehicle_id: order.vehicle_id ? String(order.vehicle_id) : '',
      tracker_id: order.tracker_id ? String(order.tracker_id) : '',
      technician_id: order.technician_id ? String(order.technician_id) : '',
      scheduled_at: toLocalDatetimeInput(order.scheduled_at),
      executed_at: toLocalDatetimeInput(order.executed_at),
      observations: order.observations || '',
      checklistItems: (order.checklist?.items && order.checklist.items.length ? order.checklist.items : checklistTemplates[order.type]).map((item) => item || ''),
    });
    setModalOpen(true);
  }

  function updateChecklist(index: number, value: string) {
    setForm((prev) => ({
      ...prev,
      checklistItems: prev.checklistItems.map((item, itemIndex) => itemIndex === index ? value : item),
    }));
  }

  function applyTemplate(type: OrderType) {
    setForm((prev) => ({ ...prev, type, checklistItems: checklistTemplates[type] }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setModalError('');
    setFeedback('');
    try {
      const payload = {
        type: form.type,
        status: form.status,
        client_id: Number(form.client_id),
        vehicle_id: form.vehicle_id ? Number(form.vehicle_id) : null,
        tracker_id: form.tracker_id ? Number(form.tracker_id) : null,
        technician_id: form.technician_id ? Number(form.technician_id) : null,
        scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : null,
        executed_at: form.executed_at ? new Date(form.executed_at).toISOString() : null,
        observations: form.observations.trim() || null,
        checklist: { items: form.checklistItems.map((item) => item.trim()).filter(Boolean) },
      };
      const saved = isEditing && selectedOrder
        ? await apiFetch<ServiceOrder>(`/service-orders/${selectedOrder.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<ServiceOrder>('/service-orders', { method: 'POST', body: JSON.stringify(payload) }, token);
      setFeedback(isEditing ? 'Ordem de serviço atualizada com sucesso.' : 'Ordem de serviço criada com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadBaseData(token);
      setSelectedOrder(saved);
      await loadOrderDetails(token, saved.id);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function updateStatus(status: OrderStatus) {
    if (!token || !selectedOrder || !canEdit) return;
    const notes = window.prompt('Observações desta etapa (opcional):', '') || '';
    try {
      const updated = await apiFetch<ServiceOrder>(`/service-orders/${selectedOrder.id}/status`, { method: 'POST', body: JSON.stringify({ status, notes: notes || null }) }, token);
      setFeedback('Status atualizado com sucesso.');
      setSelectedOrder(updated);
      await loadBaseData(token);
      await loadOrderDetails(token, selectedOrder.id);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function uploadDocuments() {
    if (!token || !selectedOrder || !docFiles.length || !canEdit) return;
    setUploading(true);
    setError('');
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/service-orders/${selectedOrder.id}/documents`, { method: 'POST', body }, token);
      setFeedback('Arquivos enviados com sucesso.');
      setDocFiles([]);
      await loadOrderDetails(token, selectedOrder.id);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function generatePdf(kind: (typeof pdfKinds)[number]['value']) {
    if (!token || !selectedOrder || !canEdit) return;
    try {
      await apiFetch<OrderDocument>(`/service-orders/${selectedOrder.id}/generate-document`, { method: 'POST', body: JSON.stringify({ kind }) }, token);
      setFeedback('Documento operacional gerado com sucesso.');
      await loadOrderDetails(token, selectedOrder.id);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function reviewDocument(documentId: number, status: ReviewStatus) {
    if (!token || !selectedOrder || !canEdit) return;
    const notes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/service-orders/${selectedOrder.id}/documents/${documentId}/review`, { method: 'PUT', body: JSON.stringify({ review_status: status, review_notes: notes || null }) }, token);
      setFeedback('Status do documento atualizado.');
      await loadOrderDetails(token, selectedOrder.id);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function deleteDocument(documentId: number) {
    if (!token || !selectedOrder || !canEdit) return;
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/service-orders/${selectedOrder.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      setFeedback('Documento removido com sucesso.');
      await loadOrderDetails(token, selectedOrder.id);
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Ordens de Serviço" description="Módulo operacional com abertura via modal, checklist por tipo, histórico por etapas e documentos integrados ao padrão do sistema.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section>
        <Card>
          <SectionHeader
            eyebrow="Operação"
            title="Carteira de ordens"
            actions={canEdit ? <Button onClick={openCreateModal}>Abrir OS</Button> : null}
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <input className={fieldClass} style={{ maxWidth: 260 }} placeholder="Buscar por OS, cliente, placa..." value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className={fieldClass} style={{ width: 160 }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Todos os status</option>
              {statusOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <select className={fieldClass} style={{ width: 180 }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">Todos os tipos</option>
              {orderTypeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <div style={{ width: 240 }}>
              <ClientAutocomplete
                clients={clients}
                value={clientFilter}
                onChange={setClientFilter}
                placeholder="Todos os clientes"
              />
            </div>
            <select className={fieldClass} style={{ width: 180 }} value={technicianFilter} onChange={(e) => setTechnicianFilter(e.target.value)}>
              <option value="">Todos os técnicos</option>
              {technicians.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <Button variant="secondary" onClick={() => token && loadBaseData(token)} disabled={loading}>
              {loading ? 'Atualizando…' : 'Atualizar'}
            </Button>
            <Button variant="ghost" onClick={() => { setSearch(''); setStatusFilter(''); setTypeFilter(''); setClientFilter(''); setVehicleFilter(''); setTechnicianFilter(''); }}>
              Limpar
            </Button>
          </div>
          <div className="mt-4">
            <OSTable
              orders={orders}
              loading={loading}
              error={error}
              canEdit={canEdit}
              onDetails={(o) => {
                setSelectedOrder(o);
                setDetailsTab('detalhes');
                setDetailsOpen(true);
                if (token) {
                  apiFetch(`/service-orders/${o.id}/logs`, {}, token).then((r) => setLogs(r as OrderLog[])).catch(() => setLogs([]));
                  apiFetch(`/service-orders/${o.id}/documents`, {}, token).then((r) => setDocuments(r as OrderDocument[])).catch(() => setDocuments([]));
                }
              }}
              onEdit={fillForm}
              statusOptions={statusOptions}
              orderTypeOptions={orderTypeOptions}
            />
          </div>
        </Card>
      </section>

      {/* Indicadores abaixo do cadastro (padrão de todas as telas) */}
      <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Ordens registradas" value={stats.total} hint="Base operacional total" icon="🧾" />
        <StatCard label="Abertas" value={stats.open} hint="Pendentes de início" tone="warning" icon="📌" />
        <StatCard label="Em andamento" value={stats.inProgress} hint="Execução em campo" tone="brand" icon="🛠️" />
        <StatCard label="Concluídas" value={stats.completed} hint="Prontas para auditoria" tone="success" icon="✅" />
      </section>

      {/* Modal de detalhes */}
      <Modal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedOrder(null); }}
        title={selectedOrder?.number ?? ''}
        subtitle="Ordem de Serviço"
        size="xl"
      >
        {selectedOrder && (
          <div className="space-y-4">
            <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
              {(['detalhes', 'documentos', 'historico'] as const).map((tab) => (
                <button key={tab} type="button" onClick={() => setDetailsTab(tab)}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${detailsTab === tab ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                  {tab === 'detalhes' ? 'Detalhes' : tab === 'documentos' ? 'Documentos' : 'Histórico'}
                </button>
              ))}
            </div>

            {/* Detalhes */}
            {detailsTab === 'detalhes' && (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    ['Status', <Badge key="s" variant={statusVariant(selectedOrder.status)}>{statusOptions.find((x) => x.value === selectedOrder.status)?.label ?? selectedOrder.status}</Badge>],
                    ['Tipo', typeLabel(selectedOrder.type)],
                    ['Cliente', selectedOrder.client_name ?? '—'],
                    ['Veículo / Rastreador', [selectedOrder.vehicle_plate, selectedOrder.tracker_label].filter(Boolean).join(' • ') || '—'],
                    ['Técnico', selectedOrder.technician_name ?? '—'],
                    ['Agendado', formatDateTimeLabel(selectedOrder.scheduled_at)],
                    ['Executado', formatDateTimeLabel(selectedOrder.executed_at)],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                      <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">{label}</p>
                      <div className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</div>
                    </div>
                  ))}
                </div>
                <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-slate-400">Checklist</p>
                  <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
                    {(selectedOrder.checklist?.items ?? []).map((item, i) => <li key={i} className="flex gap-2"><span className="text-brand-500">✓</span>{item}</li>)}
                    {!(selectedOrder.checklist?.items?.length) && <li className="text-slate-400">Sem checklist configurado.</li>}
                  </ul>
                  {selectedOrder.observations && <p className="mt-3 text-sm text-slate-500">{selectedOrder.observations}</p>}
                </div>
                {canEdit && (
                  <div>
                    <p className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-slate-400">Alterar status</p>
                    <div className="flex flex-wrap gap-2">
                      {statusOptions.map((opt) => (
                        <button key={opt.value} type="button" onClick={() => updateStatus(opt.value)}
                          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors border ${selectedOrder.status === opt.value ? 'bg-brand-700 text-white border-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800'}`}
                        >{opt.label}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Documentos */}
            {detailsTab === 'documentos' && (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  {pdfKinds.map((item) => <Button key={item.value} variant="secondary" onClick={() => generatePdf(item.value)} className="text-xs px-3 py-1.5">{item.label}</Button>)}
                </div>
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <select className={fieldClass} style={{ width: 200 }} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
                      {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input type="file" multiple className={`${fieldClass} file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:text-white`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                    <Button disabled={!docFiles.length || uploading} onClick={uploadDocuments}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
                  </div>
                )}
                {documents.length === 0 ? (
                  <EmptyState title="Sem documentos" description="Nenhum documento vinculado a esta OS." />
                ) : (
                  <div className="space-y-2">
                    {documents.map((doc) => (
                      <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p className="text-sm font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                            <p className="text-xs text-slate-400">{doc.category}</p>
                          </div>
                          <Badge variant={statusVariant(doc.review_status)}>{statusLabel(doc.review_status)}</Badge>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Visualizar</a>
                          <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Baixar</a>
                          {canEdit && (
                            <>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                              <button type="button" onClick={() => deleteDocument(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Histórico */}
            {detailsTab === 'historico' && (
              <div>
                {logs.length === 0 ? (
                  <EmptyState title="Sem histórico" description="Nenhuma mudança de status registrada." />
                ) : (
                  <ol className="relative border-l border-slate-200 dark:border-slate-700">
                    {logs.map((log) => (
                      <li key={log.id} className="mb-4 ml-5">
                        <span className="absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 ring-2 ring-white dark:bg-slate-800 dark:ring-slate-950">
                          <span className="h-2 w-2 rounded-full bg-brand-500" />
                        </span>
                        <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                          <p className="text-xs font-semibold text-slate-900 dark:text-white">
                            {log.previous_status && <><Badge variant={statusVariant(log.previous_status)}>{statusOptions.find((x) => x.value === log.previous_status)?.label}</Badge>{' → '}</>}
                            <Badge variant={statusVariant(log.new_status)}>{statusOptions.find((x) => x.value === log.new_status)?.label}</Badge>
                          </p>
                          <time className="mt-0.5 block text-[10px] text-slate-400">{formatDateTimeLabel(log.created_at)} · {log.changed_by_name ?? 'Sistema'}</time>
                          {log.notes && <p className="mt-1 text-xs text-slate-500">{log.notes}</p>}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar ordem de serviço' : 'Nova ordem de serviço'} description="Abra a ordem via modal, selecione o tipo de atendimento e configure o checklist conforme o serviço.">
        <form className="space-y-6" onSubmit={handleSubmit}>
          {modalError && (
            <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <select className={fieldClass} value={form.type} onChange={(e) => applyTemplate(e.target.value as OrderType)}>
              {orderTypeOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as OrderStatus }))}>
              {statusOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <ClientAutocomplete
              clients={clients}
              value={form.client_id}
              onChange={(id) => setForm((prev) => ({ ...prev, client_id: id, vehicle_id: '', tracker_id: '' }))}
              placeholder="Digite nome ou CPF/CNPJ…"
              required
            />
            <select className={fieldClass} value={form.vehicle_id} onChange={(e) => setForm((prev) => ({ ...prev, vehicle_id: e.target.value, tracker_id: '' }))}>
              <option value="">Selecione o veículo</option>
              {filteredVehicles.map((item) => <option key={item.id} value={item.id}>{item.plate} {item.model ? `• ${item.model}` : ''}</option>)}
            </select>
            <select className={fieldClass} value={form.tracker_id} onChange={(e) => setForm((prev) => ({ ...prev, tracker_id: e.target.value }))}>
              <option value="">Selecione o rastreador</option>
              {filteredTrackers.map((item) => <option key={item.id} value={item.id}>{item.imei}</option>)}
            </select>
            <select className={fieldClass} value={form.technician_id} onChange={(e) => setForm((prev) => ({ ...prev, technician_id: e.target.value }))}>
              <option value="">Selecione o técnico</option>
              {technicians.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-300">Agendamento</span><input type="datetime-local" className={fieldClass} value={form.scheduled_at} onChange={(e) => setForm((prev) => ({ ...prev, scheduled_at: e.target.value }))} /></label>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-300">Execução</span><input type="datetime-local" className={fieldClass} value={form.executed_at} onChange={(e) => setForm((prev) => ({ ...prev, executed_at: e.target.value }))} /></label>
          </div>

          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900 dark:text-white">Checklist por tipo de serviço</p>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Use o template do tipo selecionado e ajuste os itens conforme a operação.</p>
              </div>
              <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => setForm((prev) => ({ ...prev, checklistItems: [...prev.checklistItems, ''] }))}>Adicionar item</button>
            </div>
            <div className="mt-4 space-y-3">
              {/* key só pelo índice: com o texto na key, cada letra digitada
                  remontava o input e o foco era perdido (bug da "1 letra só") */}
              {form.checklistItems.map((item, index) => (
                <div key={index} className="flex items-center gap-3">
                  <input className={fieldClass} value={item} onChange={(e) => updateChecklist(index, e.target.value)} placeholder={`Item ${index + 1}`} />
                  <button type="button" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700" onClick={() => setForm((prev) => ({ ...prev, checklistItems: prev.checklistItems.filter((_, itemIndex) => itemIndex !== index).length ? prev.checklistItems.filter((_, itemIndex) => itemIndex !== index) : [''] }))}>Remover</button>
                </div>
              ))}
            </div>
          </div>

          <textarea className={areaClass} placeholder="Observações da ordem de serviço" value={form.observations} onChange={(e) => setForm((prev) => ({ ...prev, observations: e.target.value }))} />

          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar ordem' : 'Criar ordem de serviço'}</Button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
