'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ClipboardList, Clock, Wrench, CheckCircle2 } from 'lucide-react';

import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { SectionHeader } from '@/components/ui/section-header';
import { StatCard } from '@/components/ui/stat-card';
import { Badge, statusVariant } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { apiFetch, apiFetchList } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import type { OrderType, OrderStatus, OrderPriority, ClientOption, VehicleOption, TrackerOption, UserOption } from '@/lib/domain-types';
import { ServiceOrderDetailModal } from './_components/service-order-detail-modal';
import {
  ServiceOrder,
  orderTypeOptions,
  statusOptions,
  priorityOptions,
  checklistTemplates,
  fieldClass,
  areaClass,
  parseError,
  toLocalDatetimeInput,
} from './_components/types';

type OrderFormState = {
  type: OrderType;
  status: OrderStatus;
  priority: OrderPriority;
  client_id: string;
  vehicle_id: string;
  tracker_id: string;
  technician_id: string;
  scheduled_at: string;
  problem_description: string;
  observations: string;
  checklistItems: string[];
};

const initialForm: OrderFormState = {
  type: 'instalacao',
  status: 'aberta',
  priority: 'normal',
  client_id: '',
  vehicle_id: '',
  tracker_id: '',
  technician_id: '',
  scheduled_at: '',
  problem_description: '',
  observations: '',
  checklistItems: [''],
};

function OSTable({
  orders,
  loading,
  error,
  canEdit,
  onDetails,
  onEdit,
}: {
  orders: ServiceOrder[];
  loading: boolean;
  error?: string;
  canEdit: boolean;
  onDetails: (o: ServiceOrder) => void;
  onEdit: (o: ServiceOrder) => void;
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
          <Th>Prioridade</Th>
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
                <p className="text-xs text-slate-500">{order.vehicle_plate ?? ''}</p>
              </Td>
              <Td className="text-sm">{order.technician_name ?? '—'}</Td>
              <Td><Badge variant={statusVariant(order.priority)}>{priorityOptions.find((x) => x.value === order.priority)?.label ?? order.priority}</Badge></Td>
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
  const [serviceProducts, setServiceProducts] = useState<{ id: number; name: string }[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<ServiceOrder | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [form, setForm] = useState<OrderFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [vehicleFilter] = useState('');
  const [technicianFilter, setTechnicianFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
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

      const [orderResult, clientResult, vehicleResult, trackerResult, userResult, productResult] = await Promise.allSettled([
        apiFetch<ServiceOrder[]>(`/service-orders?${query.toString()}`, {}, currentToken),
        apiFetchList<ClientOption>('/clients?limit=200', {}, currentToken),
        apiFetchList<VehicleOption>('/vehicles?limit=500', {}, currentToken),
        apiFetchList<TrackerOption>('/trackers?limit=200', {}, currentToken),
        apiFetch<UserOption[]>('/users', {}, currentToken),
        apiFetch<{ id: number; name: string }[]>('/service-products', {}, currentToken),
      ]);

      const orderResponse = orderResult.status === 'fulfilled' ? orderResult.value : [];
      const clientResponse = clientResult.status === 'fulfilled' ? clientResult.value : [];
      const vehicleResponse = vehicleResult.status === 'fulfilled' ? vehicleResult.value : [];
      const trackerResponse = trackerResult.status === 'fulfilled' ? trackerResult.value : [];
      const userResponse = userResult.status === 'fulfilled' ? userResult.value : [];
      const productResponse = productResult.status === 'fulfilled' ? productResult.value : [];

      if (orderResult.status === 'rejected') {
        throw orderResult.reason;
      }

      setOrders(orderResponse);
      setClients(clientResponse);
      setVehicles(vehicleResponse);
      setTrackers(trackerResponse);
      setTechnicians(userResponse.filter((item) => item.role === 'admin' || item.role === 'operacional'));
      setServiceProducts(productResponse);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadBaseData(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Busca/filtros dinâmicos (sem precisar clicar em "Filtrar")
  const searchDebounced = useDebouncedValue(search);
  useEffectSkipFirst(() => {
    if (token) loadBaseData(token);
  }, [searchDebounced, statusFilter, typeFilter, clientFilter, vehicleFilter, technicianFilter]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
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
      priority: order.priority,
      client_id: order.client_id ? String(order.client_id) : '',
      vehicle_id: order.vehicle_id ? String(order.vehicle_id) : '',
      tracker_id: order.tracker_id ? String(order.tracker_id) : '',
      technician_id: order.technician_id ? String(order.technician_id) : '',
      scheduled_at: toLocalDatetimeInput(order.scheduled_at),
      problem_description: order.problem_description || '',
      observations: order.observations || '',
      checklistItems: (order.checklist && order.checklist.length ? order.checklist.map((i) => i.description) : checklistTemplates[order.type]).map((item) => item || ''),
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
        priority: form.priority,
        client_id: Number(form.client_id),
        vehicle_id: form.vehicle_id ? Number(form.vehicle_id) : null,
        tracker_id: form.tracker_id ? Number(form.tracker_id) : null,
        technician_id: form.technician_id ? Number(form.technician_id) : null,
        scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : null,
        problem_description: form.problem_description.trim() || null,
        observations: form.observations.trim() || null,
        checklist: form.checklistItems.map((item) => item.trim()).filter(Boolean).map((description) => ({ description, done: false, notes: null })),
      };
      const saved = isEditing && selectedOrder
        ? await apiFetch<ServiceOrder>(`/service-orders/${selectedOrder.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<ServiceOrder>('/service-orders', { method: 'POST', body: JSON.stringify(payload) }, token);
      setFeedback(isEditing ? 'Ordem de serviço atualizada com sucesso.' : 'Ordem de serviço criada com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadBaseData(token);
      setSelectedOrder(saved);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  function openDetails(order: ServiceOrder) {
    setSelectedOrder(order);
    setDetailsOpen(true);
  }

  return (
    <PageShell title="Ordens de Serviço" description="Módulo operacional completo: abertura via modal, checklist técnico, materiais, fotos, assinatura digital e geração de documento profissional (PDF/DOCX).">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{guardError || error}</p> : null}
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
            <Button variant="ghost" onClick={() => { setSearch(''); setStatusFilter(''); setTypeFilter(''); setClientFilter(''); setTechnicianFilter(''); }}>
              Limpar
            </Button>
          </div>
          <div className="mt-4">
            <OSTable
              orders={orders}
              loading={loading}
              error={error}
              canEdit={canEdit}
              onDetails={openDetails}
              onEdit={fillForm}
            />
          </div>
        </Card>
      </section>

      {/* Indicadores abaixo do cadastro (padrão de todas as telas) */}
      <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Ordens registradas" value={stats.total} hint="Base operacional total" icon={<ClipboardList className="h-5 w-5" />} />
        <StatCard label="Abertas" value={stats.open} hint="Pendentes de início" tone="warning" icon={<Clock className="h-5 w-5" />} />
        <StatCard label="Em andamento" value={stats.inProgress} hint="Execução em campo" tone="brand" icon={<Wrench className="h-5 w-5" />} />
        <StatCard label="Concluídas" value={stats.completed} hint="Prontas para auditoria" tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
      </section>

      <ServiceOrderDetailModal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedOrder(null); }}
        orderId={selectedOrder?.id ?? null}
        token={token ?? ''}
        canEdit={canEdit}
        onOrderChanged={() => token && loadBaseData(token)}
        serviceProducts={serviceProducts}
      />

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar ordem de serviço' : 'Nova ordem de serviço'} description="Abra a ordem via modal, selecione o tipo de atendimento e configure o checklist conforme o serviço.">
        <form className="space-y-6" onSubmit={handleSubmit}>
          {modalError && (
            <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{modalError}</p>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <select className={fieldClass} value={form.type} onChange={(e) => applyTemplate(e.target.value as OrderType)}>
              {orderTypeOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as OrderStatus }))}>
              {statusOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <select className={fieldClass} value={form.priority} onChange={(e) => setForm((prev) => ({ ...prev, priority: e.target.value as OrderPriority }))}>
              {priorityOptions.map((item) => <option key={item.value} value={item.value}>Prioridade {item.label}</option>)}
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
          </div>

          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-300">Descrição do problema relatado</span>
            <textarea className={areaClass} placeholder="O que o cliente relatou / motivo da abertura" value={form.problem_description} onChange={(e) => setForm((prev) => ({ ...prev, problem_description: e.target.value }))} />
          </label>

          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900 dark:text-white">Checklist por tipo de serviço</p>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Use o template do tipo selecionado e ajuste os itens conforme a operação — marcar como feito é na aba Checklist, dentro dos Detalhes da OS.</p>
              </div>
              <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => setForm((prev) => ({ ...prev, checklistItems: [...prev.checklistItems, ''] }))}>Adicionar item</button>
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
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar ordem' : 'Criar ordem de serviço'}</Button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
