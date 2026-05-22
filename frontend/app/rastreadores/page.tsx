'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { ExportButton } from '@/components/ui/export-button';
import { apiFetch } from '@/lib/api';
import { onlyDigits, formatCpfCnpj } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';

type TrackerStatus = 'instalado' | 'em_estoque' | 'em_manutencao' | 'extraviado' | 'descartado';

type Tracker = {
  id: number;
  imei?: string | null;
  brand?: string | null;
  model?: string | null;
  status: TrackerStatus;
  firmware?: string | null;
  external_manufacturer_id?: number | null;
  external_manufacturer_label?: string | null;
  sim_number?: string | null;
  sim_iccid?: string | null;
  notes?: string | null;
  acquisition_date?: string | null;
  install_date?: string | null;
  warranty_until?: string | null;
  client_id?: number | null;
  vehicle_id?: number | null;
  client_name?: string | null;
  client_cpf_cnpj?: string | null;
  vehicle_plate?: string | null;
  active_plan_id?: number | null;
  active_plan_name?: string | null;
  integration_status?: string | null;
  integration_last_code?: string | null;
  integration_last_description?: string | null;
};

type ClientOption = { id: number; name: string; cpf_cnpj: string; billing_day?: number | null };
type VehicleOption = { id: number; client_id: number; plate: string; model?: string | null };
type ManufacturerOption = { code: string; description: string };
type PlanOption = { id: number; name: string; price: number };
type TrackerHistory = { id: number; action: string; previous_vehicle_id?: number | null; new_vehicle_id?: number | null; previous_client_id?: number | null; new_client_id?: number | null; new_status?: string | null; event_date?: string | null; notes?: string | null; created_at?: string | null };
type ContractInfo = { id: number; plan_name?: string | null; status: string; monthly_value?: number | null; start_date?: string | null; next_due_date?: string | null };

type TrackerFormState = {
  imei: string;
  brand: string;
  model: string;
  status: TrackerStatus;
  firmware: string;
  external_manufacturer_id: string;
  external_manufacturer_label: string;
  sim_number: string;
  sim_iccid: string;
  acquisition_date: string;
  install_date: string;
  warranty_until: string;
  notes: string;
  client_id: string;
  vehicle_id: string;
  client_lookup_document: string;
  link_plan_id: string;
  link_start_date: string;
  link_billing_day: string;
  link_payment_method: string;
  link_billing_cycles: string;
};

const initialForm: TrackerFormState = {
  imei: '',
  brand: '',
  model: '',
  status: 'em_estoque',
  firmware: '',
  external_manufacturer_id: '',
  external_manufacturer_label: '',
  sim_number: '',
  sim_iccid: '',
  acquisition_date: '',
  install_date: '',
  warranty_until: '',
  notes: '',
  client_id: '',
  vehicle_id: '',
  client_lookup_document: '',
  link_plan_id: '',
  link_start_date: new Date().toISOString().split('T')[0],
  link_billing_day: '',
  link_payment_method: '',
  link_billing_cycles: '12',
};

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';
const areaClass = `${fieldClass} min-h-[88px] resize-y`;
const statusOptions: TrackerStatus[] = ['em_estoque', 'instalado', 'em_manutencao', 'extraviado', 'descartado'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function integrationVariant(status?: string | null): 'success' | 'danger' | 'default' {
  if (status === 'sincronizado') return 'success';
  if (status === 'erro') return 'danger';
  return 'default';
}

function friendlyAction(value: string) {
  const map: Record<string, string> = {
    created: 'Cadastro inicial',
    linked: 'Vínculo atualizado',
    unlinked: 'Desvínculo',
    updated: 'Dados atualizados',
    status_changed: 'Status alterado',
    deleted: 'Exclusão lógica',
  };
  return map[value] || value;
}


// ---------------------------------------------------------------------------
// Sub-component para isolar o hook de paginação
// ---------------------------------------------------------------------------

function RastreadoresTableContent({
  trackers,
  loading,
  canEdit,
  onDetails,
  onEdit,
}: {
  trackers: Tracker[];
  loading: boolean;
  canEdit: boolean;
  onDetails: (t: Tracker) => void;
  onEdit: (t: Tracker) => void;
}) {
  const pg = usePagination(trackers, 20);

  if (loading) return <TableSkeleton rows={8} cols={5} />;
  if (trackers.length === 0) return <EmptyState title="Nenhum rastreador encontrado" description="Ajuste os filtros ou adicione um novo rastreador." />;

  return (
    <>
      <Table>
        <TableHead>
          <Th>IMEI / ID</Th>
          <Th>Equipamento</Th>
          <Th>Cliente / Veículo</Th>
          <Th>Status</Th>
          <Th className="w-36" />
        </TableHead>
        <TableBody>
          {pg.slice.map((tracker) => (
            <Tr key={tracker.id}>
              <Td className="font-mono text-sm">{tracker.imei}</Td>
              <Td>
                <p>{[tracker.brand, tracker.model].filter(Boolean).join(' ') || '—'}</p>
                {tracker.active_plan_name && <p className="text-xs text-brand-600 dark:text-brand-400">{tracker.active_plan_name}</p>}
              </Td>
              <Td>
                <p className="text-sm">{tracker.client_name ?? '—'}</p>
                <p className="text-xs text-slate-400">{tracker.vehicle_plate ? `Placa ${tracker.vehicle_plate}` : ''}</p>
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  <Badge variant={statusVariant(tracker.status)}>{statusLabel(tracker.status)}</Badge>
                  {tracker.integration_status && (
                    <Badge variant={integrationVariant(tracker.integration_status)}>{tracker.integration_status}</Badge>
                  )}
                </div>
              </Td>
              <Td>
                <div className="flex justify-end gap-1.5">
                  <Button variant="secondary" onClick={() => onDetails(tracker)} className="px-3 py-1.5 text-xs">Detalhes</Button>
                  {canEdit && <Button variant="secondary" onClick={() => onEdit(tracker)} className="px-3 py-1.5 text-xs">Editar</Button>}
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

export default function RastreadoresPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [trackers, setTrackers] = useState<Tracker[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [manufacturers, setManufacturers] = useState<ManufacturerOption[]>([]);
  const [plans, setPlans] = useState<PlanOption[]>([]);
  const [history, setHistory] = useState<TrackerHistory[]>([]);
  const [trackerContract, setTrackerContract] = useState<ContractInfo | null>(null);
  const [vehicleTrackers, setVehicleTrackers] = useState<Tracker[]>([]);
  const [selectedTracker, setSelectedTracker] = useState<Tracker | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<'dados' | 'historico' | 'contrato'>('dados');
  const [form, setForm] = useState<TrackerFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [vehicleFilter, setVehicleFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [manufacturerError, setManufacturerError] = useState('');

  async function loadBaseData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search) query.set('search', search);
      if (statusFilter) query.set('status', statusFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      if (vehicleFilter) query.set('vehicle_id', vehicleFilter);
      query.set('limit', '100');
      const [trackerResponse, clientResponse, vehicleResponse, planResponse] = await Promise.all([
        apiFetch<Tracker[]>(`/trackers?${query.toString()}`, {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=300', {}, currentToken),
        apiFetch<VehicleOption[]>('/vehicles?limit=400', {}, currentToken),
        apiFetch<PlanOption[]>('/plans?limit=100', {}, currentToken).catch(() => [] as PlanOption[]),
      ]);
      setTrackers(trackerResponse);
      setClients(clientResponse);
      setVehicles(vehicleResponse);
      setPlans(planResponse);
      try {
        const manufacturerResponse = await apiFetch<ManufacturerOption[]>('/integrations/multiportal/manufacturers', {}, currentToken);
        setManufacturers(manufacturerResponse);
        setManufacturerError('');
      } catch (err) {
        setManufacturers([]);
        setManufacturerError(parseError(err));
      }
      if (selectedTracker) setSelectedTracker(trackerResponse.find((item) => item.id === selectedTracker.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadBaseData(token);
  }, [token]);

  useEffect(() => {
    if (!token || !selectedTracker) {
      setHistory([]);
      setTrackerContract(null);
      setVehicleTrackers([]);
      return;
    }
    apiFetch<TrackerHistory[]>(`/trackers/${selectedTracker.id}/history`, {}, token).then(setHistory).catch(() => setHistory([]));
    apiFetch<ContractInfo[]>(`/contracts?tracker_id=${selectedTracker.id}&status=ativo`, {}, token)
      .then((items) => setTrackerContract(items[0] || null))
      .catch(() => setTrackerContract(null));
    if (selectedTracker.vehicle_id) {
      apiFetch<Tracker[]>(`/trackers?vehicle_id=${selectedTracker.vehicle_id}&limit=20`, {}, token)
        .then((items) => setVehicleTrackers(items.filter((t) => t.id !== selectedTracker.id)))
        .catch(() => setVehicleTrackers([]));
    } else {
      setVehicleTrackers([]);
    }
  }, [token, selectedTracker?.id]);

  const filteredVehicles = useMemo(() => {
    if (!form.client_id) return vehicles;
    return vehicles.filter((item) => item.client_id === Number(form.client_id));
  }, [vehicles, form.client_id]);

  const vehicleExistingTrackers = useMemo(() => {
    if (!form.vehicle_id) return [];
    return trackers.filter((t) => t.vehicle_id === Number(form.vehicle_id) && (!selectedTracker || t.id !== selectedTracker.id));
  }, [trackers, form.vehicle_id, selectedTracker]);

  const stats = useMemo(() => ({
    total: trackers.length,
    installed: trackers.filter((item) => item.status === 'instalado').length,
    stock: trackers.filter((item) => item.status === 'em_estoque').length,
    maintenance: trackers.filter((item) => item.status === 'em_manutencao').length,
  }), [trackers]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(tracker: Tracker) {
    setSelectedTracker(tracker);
    setModalError('');
    setForm({
      imei: tracker.imei || '',
      brand: tracker.brand || '',
      model: tracker.model || '',
      status: tracker.status,
      firmware: tracker.firmware || '',
      external_manufacturer_id: tracker.external_manufacturer_id ? String(tracker.external_manufacturer_id) : '',
      external_manufacturer_label: tracker.external_manufacturer_label || '',
      sim_number: tracker.sim_number || '',
      sim_iccid: tracker.sim_iccid || '',
      acquisition_date: tracker.acquisition_date || '',
      install_date: tracker.install_date || '',
      warranty_until: tracker.warranty_until || '',
      notes: tracker.notes || '',
      client_id: tracker.client_id ? String(tracker.client_id) : '',
      vehicle_id: tracker.vehicle_id ? String(tracker.vehicle_id) : '',
      client_lookup_document: tracker.client_cpf_cnpj ? formatCpfCnpj(tracker.client_cpf_cnpj) : '',
      link_plan_id: '',
      link_start_date: new Date().toISOString().split('T')[0],
      link_billing_day: '',
      link_payment_method: '',
      link_billing_cycles: '12',
    });
    setIsEditing(true);
    setModalOpen(true);
  }

  function findClientByDocument() {
    const digits = onlyDigits(form.client_lookup_document);
    const match = clients.find((item) => onlyDigits(item.cpf_cnpj) === digits);
    if (!match) {
      setError('Nenhum cliente encontrado com o CPF/CNPJ informado.');
      return;
    }
    setError('');
    setForm((prev) => ({ ...prev, client_id: String(match.id), vehicle_id: '' }));
  }

  async function submitTracker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setError('');
    setFeedback('');
    try {
      const selectedManufacturer = manufacturers.find((item) => item.code === form.external_manufacturer_id);
      const isLinkingVehicle = !!form.vehicle_id && (!selectedTracker || selectedTracker.vehicle_id !== Number(form.vehicle_id));

      const payload = {
        imei: onlyDigits(form.imei),
        brand: form.brand.trim() || null,
        model: form.model.trim() || null,
        status: form.status,
        firmware: form.firmware.trim() || null,
        external_manufacturer_id: form.external_manufacturer_id ? Number(form.external_manufacturer_id) : null,
        external_manufacturer_label: selectedManufacturer?.description || form.external_manufacturer_label || null,
        sim_number: onlyDigits(form.sim_number) || null,
        sim_iccid: onlyDigits(form.sim_iccid) || null,
        acquisition_date: form.acquisition_date || null,
        install_date: form.install_date || null,
        warranty_until: form.warranty_until || null,
        notes: form.notes.trim() || null,
        client_id: form.client_id ? Number(form.client_id) : null,
        vehicle_id: form.vehicle_id ? Number(form.vehicle_id) : null,
      };

      let saved: Tracker;
      if (isEditing && selectedTracker) {
        saved = await apiFetch<Tracker>(`/trackers/${selectedTracker.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token);

        // Se está vinculando a um veículo novo e selecionou plano → usar endpoint dedicado
        if (isLinkingVehicle && form.link_plan_id) {
          await apiFetch(`/trackers/${selectedTracker.id}/link-vehicle`, {
            method: 'POST',
            body: JSON.stringify({
              vehicle_id: Number(form.vehicle_id),
              plan_id: Number(form.link_plan_id),
              start_date: form.link_start_date,
              billing_day: form.link_billing_day ? Number(form.link_billing_day) : null,
              payment_method: form.link_payment_method || null,
              auto_generate_billings: true,
              billing_cycles: Number(form.link_billing_cycles) || 12,
            }),
          }, token);
        }
      } else {
        saved = await apiFetch<Tracker>('/trackers', { method: 'POST', body: JSON.stringify(payload) }, token);

        // Após criar, se selecionou veículo + plano → vincular com contrato
        if (form.vehicle_id && form.link_plan_id) {
          await apiFetch(`/trackers/${saved.id}/link-vehicle`, {
            method: 'POST',
            body: JSON.stringify({
              vehicle_id: Number(form.vehicle_id),
              plan_id: Number(form.link_plan_id),
              start_date: form.link_start_date,
              billing_day: form.link_billing_day ? Number(form.link_billing_day) : null,
              payment_method: form.link_payment_method || null,
              auto_generate_billings: true,
              billing_cycles: Number(form.link_billing_cycles) || 12,
            }),
          }, token);
        }
      }

      setFeedback(isEditing ? 'Rastreador atualizado com sucesso.' : 'Rastreador cadastrado com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadBaseData(token);
      setSelectedTracker(saved);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteTracker() {
    if (!token || !selectedTracker || !canEdit) return;
    if (!window.confirm(`Deseja remover o rastreador ${selectedTracker.imei}?`)) return;
    try {
      await apiFetch(`/trackers/${selectedTracker.id}`, { method: 'DELETE' }, token);
      setFeedback('Rastreador removido com sucesso.');
      setSelectedTracker(null);
      await loadBaseData(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Rastreadores" description="Base técnica dos dispositivos com cadastro em modal, busca por cliente e visão consolidada do vínculo operacional.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Rastreadores cadastrados" value={stats.total} hint="Base técnica disponível" icon="📡" />
        <StatCard label="Instalados" value={stats.installed} hint="Equipamentos em produção" tone="success" icon="✅" />
        <StatCard label="Em estoque" value={stats.stock} hint="Prontos para reutilização" tone="brand" icon="📦" />
        <StatCard label="Em manutenção" value={stats.maintenance} hint="Exigem acompanhamento" tone="warning" icon="🛠️" />
      </section>

      <section className="mt-6">
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Controle de rastreadores"
            actions={
              <div className="flex items-center gap-2">
                {token && <ExportButton path="exports/trackers" basename="rastreadores" token={token} params={{ status: statusFilter, client_id: clientFilter }} />}
                {canEdit && <Button onClick={openCreateModal}>Adicionar rastreador</Button>}
              </div>
            }
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <input className={fieldClass} style={{ maxWidth: 280 }} placeholder="IMEI, modelo ou placa" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className={fieldClass} style={{ width: 180 }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Todos os status</option>
              {statusOptions.map((o) => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
            </select>
            <select className={fieldClass} style={{ width: 220 }} value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}>
              <option value="">Todos os clientes</option>
              {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <Button variant="secondary" onClick={() => token && loadBaseData(token)} disabled={loading}>
              {loading ? 'Atualizando…' : 'Filtrar'}
            </Button>
          </div>
          <div className="mt-4">
            <RastreadoresTableContent
              trackers={trackers}
              loading={loading}
              canEdit={canEdit}
              onDetails={(t) => { setSelectedTracker(t); setDetailsTab('dados'); setDetailsOpen(true); }}
              onEdit={openEditModal}
            />
          </div>
        </Card>
      </section>

      {/* Modal de detalhes */}
      <Modal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedTracker(null); }}
        title={selectedTracker?.imei ?? ''}
        subtitle="Detalhes do rastreador"
        size="lg"
        footer={canEdit && selectedTracker ? (
          <div className="flex justify-end">
            <Button variant="danger" onClick={() => { setDetailsOpen(false); deleteTracker(); }} className="text-xs">
              Excluir rastreador
            </Button>
          </div>
        ) : undefined}
      >
        {selectedTracker && (
          <div className="space-y-4">
            <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
              {(['dados', 'historico', 'contrato'] as const).map((tab) => (
                <button key={tab} type="button" onClick={() => setDetailsTab(tab)}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${detailsTab === tab ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                  {tab === 'dados' ? 'Dados' : tab === 'historico' ? 'Histórico' : 'Contrato'}
                </button>
              ))}
            </div>

            {detailsTab === 'dados' && (
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  ['Status', <Badge key="s" variant={statusVariant(selectedTracker.status)}>{statusLabel(selectedTracker.status)}</Badge>],
                  ['Integração', <Badge key="i" variant={integrationVariant(selectedTracker.integration_status)}>{selectedTracker.integration_status ?? 'sem sync'}</Badge>],
                  ['Marca / Modelo', [selectedTracker.brand, selectedTracker.model].filter(Boolean).join(' ') || '—'],
                  ['Fabricante', selectedTracker.external_manufacturer_label ?? selectedTracker.brand ?? '—'],
                  ['Cliente', selectedTracker.client_name ?? '—'],
                  ['Veículo', selectedTracker.vehicle_plate ?? '—'],
                  ['Linha SIM', selectedTracker.sim_number ?? '—'],
                  ['ICCID', selectedTracker.sim_iccid ?? '—'],
                  ['Firmware', selectedTracker.firmware ?? '—'],
                  ['Data instalação', selectedTracker.install_date ?? '—'],
                  ['Garantia até', selectedTracker.warranty_until ?? '—'],
                  ['Aquisição', selectedTracker.acquisition_date ?? '—'],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">{label}</p>
                    <div className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</div>
                  </div>
                ))}
                {selectedTracker.notes && (
                  <div className="col-span-2 rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Observações</p>
                    <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{selectedTracker.notes}</p>
                  </div>
                )}
                {vehicleTrackers.length > 0 && (
                  <div className="col-span-2 space-y-1.5">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Outros rastreadores neste veículo</p>
                    {vehicleTrackers.map((t) => (
                      <div key={t.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/50">
                        <span className="font-mono text-sm">{t.imei}</span>
                        <span className="text-xs text-slate-400">{t.active_plan_name ?? 'sem plano'}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {detailsTab === 'historico' && (
              <div>
                {history.length === 0 ? (
                  <EmptyState title="Sem histórico" description="Nenhum evento registrado." />
                ) : (
                  <ol className="relative border-l border-slate-200 dark:border-slate-700">
                    {history.map((entry) => (
                      <li key={entry.id} className="mb-4 ml-5">
                        <span className="absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 ring-2 ring-white dark:bg-slate-800 dark:ring-slate-950">
                          <span className="h-2 w-2 rounded-full bg-brand-500" />
                        </span>
                        <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-xs font-semibold text-slate-900 dark:text-white">{friendlyAction(entry.action)}</p>
                            {entry.new_status && <Badge variant={statusVariant(entry.new_status)}>{statusLabel(entry.new_status)}</Badge>}
                          </div>
                          <time className="mt-0.5 block text-[10px] text-slate-400">{entry.created_at ? new Date(entry.created_at).toLocaleString('pt-BR') : entry.event_date ?? '—'}</time>
                          {entry.notes && <p className="mt-1 text-xs text-slate-500">{entry.notes}</p>}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}

            {detailsTab === 'contrato' && (
              <div>
                {trackerContract ? (
                  <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/30">
                    <p className="text-xs font-semibold uppercase tracking-widest text-emerald-600 dark:text-emerald-400">Contrato ativo</p>
                    <p className="mt-2 text-lg font-semibold text-slate-900 dark:text-white">{trackerContract.plan_name ?? 'Plano'}</p>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2 text-sm text-slate-600 dark:text-slate-300">
                      {trackerContract.monthly_value != null && <div><span className="font-medium">Valor:</span> R$ {trackerContract.monthly_value.toFixed(2)}/mês</div>}
                      {trackerContract.start_date && <div><span className="font-medium">Início:</span> {trackerContract.start_date}</div>}
                      {trackerContract.next_due_date && <div><span className="font-medium">Próx. venc.:</span> {trackerContract.next_due_date}</div>}
                    </div>
                  </div>
                ) : (
                  <EmptyState title="Sem contrato ativo" description={selectedTracker.vehicle_id ? 'Nenhum contrato ativo — gerencie em Financeiro.' : 'Vincule o rastreador a um veículo pela página de Veículos.'} />
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar rastreador' : 'Novo rastreador'} description="Cadastre o equipamento em um fluxo mais limpo, com foco no identificador técnico, vínculo e dados essenciais." size="xl">
        <form className="space-y-6" onSubmit={submitTracker}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Número de série / ID" value={form.imei} onChange={(e) => setForm((prev) => ({ ...prev, imei: onlyDigits(e.target.value).slice(0, 20) }))} required />
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as TrackerStatus }))}>{statusOptions.map((option) => <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>)}</select>
            <input className={fieldClass} placeholder="Marca" value={form.brand} onChange={(e) => setForm((prev) => ({ ...prev, brand: e.target.value.slice(0, 60) }))} required />
            <input className={fieldClass} placeholder="Modelo" value={form.model} onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value.slice(0, 60) }))} required />
          </div>

          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Vínculo do cliente</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Busque por CPF/CNPJ e o sistema selecionará automaticamente o cliente correspondente.</p>
            <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto]">
              <input className={fieldClass} placeholder="CPF/CNPJ do cliente" value={form.client_lookup_document} onChange={(e) => setForm((prev) => ({ ...prev, client_lookup_document: formatCpfCnpj(e.target.value) }))} />
              <Button type="button" onClick={findClientByDocument}>Buscar cliente</Button>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <select className={fieldClass} value={form.client_id} onChange={(e) => setForm((prev) => ({ ...prev, client_id: e.target.value, vehicle_id: '', link_plan_id: '' }))}><option value="">Sem cliente</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
              <select className={fieldClass} value={form.vehicle_id} onChange={(e) => {
                const vid = e.target.value;
                const veh = filteredVehicles.find((v) => String(v.id) === vid);
                const cli = veh ? clients.find((c) => c.id === veh.client_id) : null;
                const autoDay = cli?.billing_day ? String(cli.billing_day) : '';
                setForm((prev) => ({ ...prev, vehicle_id: vid, link_billing_day: autoDay }));
              }}><option value="">Sem veículo</option>{filteredVehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.plate} {vehicle.model ? `• ${vehicle.model}` : ''}</option>)}</select>
            </div>
          </div>

          {form.vehicle_id && (
            <div className="rounded-[24px] border border-brand-200 bg-brand-50/50 p-5 dark:border-cyan-900 dark:bg-cyan-950/30">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">Plano contratado</p>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Selecione o plano para criar o contrato automaticamente ao vincular. Deixe em branco para vincular sem contrato.</p>
              {vehicleExistingTrackers.length > 0 && (
                <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-950/40">
                  <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">Este veículo já possui {vehicleExistingTrackers.length} rastreador(es) instalado(s):</p>
                  <ul className="mt-1 space-y-0.5">
                    {vehicleExistingTrackers.map((t) => (
                      <li key={t.id} className="text-xs text-amber-600 dark:text-amber-300">• {t.imei} — {t.active_plan_name || 'sem plano'}</li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-amber-600 dark:text-amber-300">Cada equipamento pode ter seu próprio plano e contrato.</p>
                </div>
              )}
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <select className={fieldClass} value={form.link_plan_id} onChange={(e) => setForm((prev) => ({ ...prev, link_plan_id: e.target.value }))}>
                  <option value="">Sem contrato agora</option>
                  {plans.map((p) => <option key={p.id} value={p.id}>{p.name} — R$ {Number(p.price ?? 0).toFixed(2)}/mês</option>)}
                </select>
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Início do contrato</span>
                  <input type="date" className={fieldClass} value={form.link_start_date} onChange={(e) => setForm((prev) => ({ ...prev, link_start_date: e.target.value }))} />
                </label>
                {form.link_plan_id && (
                  <>
                    <select className={fieldClass} value={form.link_payment_method} onChange={(e) => setForm((prev) => ({ ...prev, link_payment_method: e.target.value }))}>
                      <option value="">Forma de pagamento</option>
                      <option value="boleto">Boleto</option>
                      <option value="pix">PIX</option>
                      <option value="cartao">Cartão</option>
                      <option value="deposito">Depósito</option>
                      <option value="dinheiro">Dinheiro</option>
                    </select>
                    {/* Dia de vencimento: herdado do cliente */}
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
                      <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Dia do vencimento</p>
                      {form.link_billing_day ? (
                        <p className="mt-1 text-sm font-bold text-slate-900 dark:text-white">
                          Todo dia <span className="text-brand-700 dark:text-brand-300">{form.link_billing_day}</span>
                          <span className="ml-2 text-xs font-normal text-slate-400"> · herdado do cliente</span>
                        </p>
                      ) : (
                        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                          Selecione um veículo para herdar automaticamente ou
                          <input
                            type="number" min={1} max={28}
                            placeholder=" informe o dia"
                            value={form.link_billing_day}
                            onChange={(e) => setForm((prev) => ({ ...prev, link_billing_day: e.target.value }))}
                            className="ml-1 w-20 rounded border border-amber-300 bg-white px-2 py-0.5 text-xs text-slate-700 dark:border-amber-700 dark:bg-slate-800 dark:text-white"
                          />
                        </p>
                      )}
                    </div>
                    <input className={fieldClass} placeholder="Ciclos (meses)" type="number" min={1} max={60} value={form.link_billing_cycles} onChange={(e) => setForm((prev) => ({ ...prev, link_billing_cycles: e.target.value }))} />
                  </>
                )}
              </div>
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1">
              <select className={fieldClass} value={form.external_manufacturer_id} onChange={(e) => { const option = manufacturers.find((item) => item.code === e.target.value); setForm((prev) => ({ ...prev, external_manufacturer_id: e.target.value, external_manufacturer_label: option?.description || '' })); }}>
                <option value="">{manufacturers.length === 0 ? 'Fabricante Multiportal (sem dados)' : 'Fabricante Multiportal'}</option>
                {manufacturers.map((option) => <option key={option.code} value={option.code}>{option.code} • {option.description}</option>)}
              </select>
              {manufacturerError && (
                <div className="flex items-center gap-2">
                  <p className="text-xs text-rose-600 dark:text-rose-400">{manufacturerError}</p>
                  <button type="button" onClick={() => token && loadBaseData(token)} className="text-xs font-semibold text-brand-600 underline dark:text-cyan-400">Recarregar</button>
                </div>
              )}
            </div>
            <input className={fieldClass} placeholder="Firmware" value={form.firmware} onChange={(e) => setForm((prev) => ({ ...prev, firmware: e.target.value.slice(0, 60) }))} />
            <input className={fieldClass} placeholder="Linha / MSISDN" value={form.sim_number} onChange={(e) => setForm((prev) => ({ ...prev, sim_number: onlyDigits(e.target.value).slice(0, 20) }))} />
            <input className={fieldClass} placeholder="ICCID" value={form.sim_iccid} onChange={(e) => setForm((prev) => ({ ...prev, sim_iccid: onlyDigits(e.target.value).slice(0, 22) }))} />
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Data de aquisição</span>
              <input type="date" className={fieldClass} value={form.acquisition_date} onChange={(e) => setForm((prev) => ({ ...prev, acquisition_date: e.target.value }))} />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Data de instalação</span>
              <input type="date" className={fieldClass} value={form.install_date} onChange={(e) => setForm((prev) => ({ ...prev, install_date: e.target.value }))} />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Garantia válida até</span>
              <input type="date" className={fieldClass} value={form.warranty_until} onChange={(e) => setForm((prev) => ({ ...prev, warranty_until: e.target.value }))} />
            </label>
            <textarea className={`${areaClass} md:col-span-3`} placeholder="Observações técnicas" value={form.notes} onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value.slice(0, 500) }))} />
          </div>

          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar rastreador' : 'Cadastrar rastreador'}</Button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
