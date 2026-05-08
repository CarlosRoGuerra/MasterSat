'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { SectionHeader } from '@/components/ui/section-header';
import { StatCard } from '@/components/ui/stat-card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';


type OrderType = 'instalacao' | 'manutencao' | 'retirada' | 'visita_tecnica';
type OrderStatus = 'aberta' | 'em_andamento' | 'concluida' | 'cancelada';
type ReviewStatus = 'enviado' | 'em_analise' | 'aprovado' | 'rejeitado' | 'reenvio_solicitado';

type ClientOption = { id: number; name: string };
type VehicleOption = { id: number; client_id: number; plate: string; model?: string | null };
type TrackerOption = { id: number; imei: string; client_id?: number | null; vehicle_id?: number | null; status: string };
type UserOption = { id: number; name: string; role: string };

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

const fieldClass = 'w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400';
const areaClass = `${fieldClass} min-h-[96px] resize-y`;

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

function statusBadge(status: OrderStatus) {
  switch (status) {
    case 'concluida':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'em_andamento':
      return 'border-cyan-200 bg-cyan-50 text-cyan-700';
    case 'cancelada':
      return 'border-rose-200 bg-rose-50 text-rose-700';
    default:
      return 'border-amber-200 bg-amber-50 text-amber-700';
  }
}

function reviewBadge(status: ReviewStatus) {
  switch (status) {
    case 'aprovado':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'rejeitado':
      return 'border-rose-200 bg-rose-50 text-rose-700';
    case 'reenvio_solicitado':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'em_analise':
      return 'border-cyan-200 bg-cyan-50 text-cyan-700';
    default:
      return 'border-slate-200 bg-slate-50 text-slate-700';
  }
}

function typeLabel(value: OrderType) {
  return orderTypeOptions.find((item) => item.value === value)?.label || value;
}

export default function ServiceOrdersPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [orders, setOrders] = useState<ServiceOrder[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [trackers, setTrackers] = useState<TrackerOption[]>([]);
  const [technicians, setTechnicians] = useState<UserOption[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<ServiceOrder | null>(null);
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
    setModalOpen(true);
  }

  function fillForm(order: ServiceOrder) {
    setSelectedOrder(order);
    setIsEditing(true);
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

  function useTemplate(type: OrderType) {
    setForm((prev) => ({ ...prev, type, checklistItems: checklistTemplates[type] }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setError('');
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
      setError(parseError(err));
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

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Ordens registradas" value={stats.total} hint="Base operacional total" icon="🧾" />
        <StatCard label="Abertas" value={stats.open} hint="Pendentes de início" tone="warning" icon="📌" />
        <StatCard label="Em andamento" value={stats.inProgress} hint="Execução em campo" tone="brand" icon="🛠️" />
        <StatCard label="Concluídas" value={stats.completed} hint="Prontas para auditoria" tone="success" icon="✅" />
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Operação" title="Carteira operacional" description="Filtre por cliente, veículo, técnico, tipo e status para acompanhar a fila de execução." actions={canEdit ? <Button type="button" onClick={openCreateModal}>Abrir ordem de serviço</Button> : null} />
            <div className="mt-5 grid gap-4 md:grid-cols-3 xl:grid-cols-6">
              <input className={fieldClass} placeholder="Buscar por OS, cliente, placa..." value={search} onChange={(e) => setSearch(e.target.value)} />
              <select className={fieldClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">Todos os status</option>{statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
              <select className={fieldClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}><option value="">Todos os tipos</option>{orderTypeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
              <select className={fieldClass} value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}><option value="">Todos os clientes</option>{clients.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select>
              <select className={fieldClass} value={vehicleFilter} onChange={(e) => setVehicleFilter(e.target.value)}><option value="">Todos os veículos</option>{vehicles.map((option) => <option key={option.id} value={option.id}>{option.plate}</option>)}</select>
              <select className={fieldClass} value={technicianFilter} onChange={(e) => setTechnicianFilter(e.target.value)}><option value="">Todos os técnicos</option>{technicians.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select>
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button type="button" onClick={() => token && loadBaseData(token)} disabled={loading}>{loading ? 'Atualizando...' : 'Aplicar filtros'}</Button>
              <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setSearch(''); setStatusFilter(''); setTypeFilter(''); setClientFilter(''); setVehicleFilter(''); setTechnicianFilter(''); token && loadBaseData(token); }}>Limpar</button>
            </div>

            <div className="mt-5 grid gap-3">
              {orders.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-6 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhuma ordem de serviço encontrada com os filtros atuais.</p> : orders.map((order) => (
                <button key={order.id} type="button" onClick={() => setSelectedOrder(order)} className={`rounded-[24px] border p-5 text-left transition ${selectedOrder?.id === order.id ? 'border-brand-500 bg-brand-50/60 shadow-sm dark:border-cyan-400 dark:bg-cyan-400/10' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-lg dark:border-slate-800 dark:bg-slate-900'}`}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-lg font-semibold text-slate-900 dark:text-white">{order.number}</p>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${statusBadge(order.status)}`}>{statusOptions.find((item) => item.value === order.status)?.label || order.status}</span>
                      </div>
                      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{typeLabel(order.type)} • {order.client_name || 'Cliente não informado'} • {order.vehicle_plate || 'Sem placa'}</p>
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Técnico: {order.technician_name || 'Não definido'} • Agendado: {formatDateTimeLabel(order.scheduled_at)}</p>
                    </div>
                    {canEdit ? <Button type="button" onClick={(event) => { event.stopPropagation(); fillForm(order); }}>Editar</Button> : null}
                  </div>
                </button>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Acompanhamento" title={selectedOrder ? selectedOrder.number : 'Selecione uma ordem'} description={selectedOrder ? 'Resumo do atendimento, checklist e status por etapa.' : 'Escolha uma ordem para ver o detalhamento, gerar documentos e lançar evidências.'} />
            {selectedOrder ? (
              <div className="mt-5 space-y-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.18em] text-slate-400">Cliente</p><p className="mt-2 text-base font-semibold text-slate-900 dark:text-white">{selectedOrder.client_name || '—'}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.18em] text-slate-400">Veículo / rastreador</p><p className="mt-2 text-base font-semibold text-slate-900 dark:text-white">{selectedOrder.vehicle_plate || '—'} {selectedOrder.tracker_label ? `• ${selectedOrder.tracker_label}` : ''}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.18em] text-slate-400">Técnico</p><p className="mt-2 text-base font-semibold text-slate-900 dark:text-white">{selectedOrder.technician_name || '—'}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.18em] text-slate-400">Execução</p><p className="mt-2 text-base font-semibold text-slate-900 dark:text-white">{formatDateTimeLabel(selectedOrder.executed_at)}</p></div>
                </div>

                <div className="rounded-[24px] border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950/60">
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">Checklist</p>
                  <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-300">
                    {(selectedOrder.checklist?.items || []).length ? (selectedOrder.checklist?.items || []).map((item, index) => <li key={`${item}-${index}`}>• {item}</li>) : <li>Checklist ainda não configurado.</li>}
                  </ul>
                  {selectedOrder.observations ? <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">{selectedOrder.observations}</p> : null}
                </div>

                {canEdit ? (
                  <div className="flex flex-wrap gap-2">
                    {statusOptions.map((option) => (
                      <button key={option.value} type="button" onClick={() => updateStatus(option.value)} className={`rounded-2xl border px-4 py-2 text-sm font-semibold transition ${selectedOrder.status === option.value ? `${statusBadge(option.value)} border-current` : 'border-slate-200 text-slate-700 hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300'}`}>{option.label}</button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">Selecione uma ordem para acompanhar o fluxo operacional.</p>}
          </Card>

          <Card>
            <SectionHeader eyebrow="Documentos operacionais" title="PDFs e evidências" description="Gere os documentos da OS e anexe evidências fotográficas e arquivos técnicos no mesmo padrão do sistema." />
            {selectedOrder ? (
              <>
                <div className="mt-5 flex flex-wrap gap-2">
                  {pdfKinds.map((item) => <Button key={item.value} type="button" onClick={() => generatePdf(item.value)} disabled={!canEdit}>{item.label}</Button>)}
                </div>
                <div className="mt-5 grid gap-3 md:grid-cols-[220px_1fr_auto]">
                  <select className={fieldClass} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
                    {documentCategoryOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                  <input type="file" multiple className={`${fieldClass} file:mr-4 file:rounded-xl file:border-0 file:bg-slate-950 file:px-3 file:py-2 file:text-white dark:file:bg-cyan-400 dark:file:text-slate-950`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                  <Button type="button" disabled={!canEdit || uploading || !docFiles.length} onClick={uploadDocuments}>{uploading ? 'Enviando...' : 'Enviar'}</Button>
                </div>
                <div className="mt-5 space-y-3">
                  {documents.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum documento vinculado a esta OS ainda.</p> : documents.map((document) => (
                    <div key={document.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900 dark:text-white">{document.file_name}</p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Categoria: {document.category}</p>
                          {document.review_notes ? <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Obs.: {document.review_notes}</p> : null}
                        </div>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${reviewBadge(document.review_status)}`}>{document.review_status.replace(/_/g, ' ')}</span>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <a href={document.url} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Visualizar</a>
                        <a href={document.download_url} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Baixar</a>
                        {canEdit ? (
                          <>
                            <button type="button" onClick={() => reviewDocument(document.id, 'aprovado')} className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700">Aprovar</button>
                            <button type="button" onClick={() => reviewDocument(document.id, 'reenvio_solicitado')} className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-700">Solicitar ajuste</button>
                            <button type="button" onClick={() => deleteDocument(document.id)} className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700">Excluir</button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">Selecione uma ordem para gerar PDFs e anexar evidências.</p>}
          </Card>

          <Card>
            <SectionHeader eyebrow="Histórico" title="Evolução por etapa" description="Linha do tempo com mudanças de status, responsável e observações de execução." />
            <div className="mt-5 space-y-3">
              {!selectedOrder ? <p className="text-sm text-slate-500 dark:text-slate-400">Selecione uma ordem para consultar o histórico.</p> : logs.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum evento registrado até o momento.</p> : logs.map((log) => (
                <div key={log.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-slate-900 dark:text-white">{(log.previous_status ? `${statusOptions.find((item) => item.value === log.previous_status)?.label || log.previous_status} → ` : '') + (statusOptions.find((item) => item.value === log.new_status)?.label || log.new_status)}</p>
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{formatDateTimeLabel(log.created_at)} • {log.changed_by_name || 'Sistema'}</p>
                    </div>
                  </div>
                  {log.notes ? <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">{log.notes}</p> : null}
                </div>
              ))}
            </div>
          </Card>
        </div>
      </section>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); }} title={isEditing ? 'Editar ordem de serviço' : 'Nova ordem de serviço'} description="Abra a ordem via modal, selecione o tipo de atendimento e configure o checklist conforme o serviço.">
        <form className="space-y-6" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <select className={fieldClass} value={form.type} onChange={(e) => useTemplate(e.target.value as OrderType)}>
              {orderTypeOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as OrderStatus }))}>
              {statusOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select className={fieldClass} value={form.client_id} onChange={(e) => setForm((prev) => ({ ...prev, client_id: e.target.value, vehicle_id: '', tracker_id: '' }))} required>
              <option value="">Selecione o cliente</option>
              {clients.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
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
              {form.checklistItems.map((item, index) => (
                <div key={`${index}-${item}`} className="flex items-center gap-3">
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
