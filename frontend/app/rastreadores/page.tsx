'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
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

type ClientOption = { id: number; name: string; cpf_cnpj: string };
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

const fieldClass = 'w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400';
const areaClass = `${fieldClass} min-h-[96px] resize-y`;
const statusOptions: TrackerStatus[] = ['em_estoque', 'instalado', 'em_manutencao', 'extraviado', 'descartado'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function statusBadge(status: TrackerStatus) {
  switch (status) {
    case 'instalado':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'em_manutencao':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'extraviado':
    case 'descartado':
      return 'border-rose-200 bg-rose-50 text-rose-700';
    default:
      return 'border-slate-200 bg-slate-50 text-slate-700';
  }
}

function integrationBadge(status?: string | null) {
  if (status === 'sincronizado') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (status === 'erro') return 'border-rose-200 bg-rose-50 text-rose-700';
  return 'border-slate-200 bg-slate-50 text-slate-700';
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


function cardKeyHandler(event: React.KeyboardEvent<HTMLElement>, callback: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    callback();
  }
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

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Cadastro" title="Controle de rastreadores" description="Separe listagem de formulários para manter o módulo técnico mais limpo e profissional." actions={canEdit ? <Button type="button" onClick={openCreateModal}>Adicionar rastreador</Button> : null} />
            <div className="mt-5 grid gap-4 md:grid-cols-4">
              <input className={fieldClass} placeholder="Buscar por número de série / ID, modelo ou placa" value={search} onChange={(e) => setSearch(e.target.value)} />
              <select className={fieldClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">Todos os status</option>{statusOptions.map((option) => <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>)}</select>
              <select className={fieldClass} value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}><option value="">Todos os clientes</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
              <Button type="button" onClick={() => token && loadBaseData(token)} disabled={loading}>{loading ? 'Atualizando...' : 'Aplicar filtros'}</Button>
            </div>
            <div className="mt-5 grid gap-3">
              {trackers.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-6 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhum rastreador encontrado.</p> : trackers.map((tracker) => (
                <div key={tracker.id} role="button" tabIndex={0} onClick={() => setSelectedTracker(tracker)} onKeyDown={(event) => cardKeyHandler(event, () => setSelectedTracker(tracker))} className={`cursor-pointer rounded-[24px] border p-5 text-left transition ${selectedTracker?.id === tracker.id ? 'border-brand-500 bg-brand-50/60 shadow-sm dark:border-cyan-400 dark:bg-cyan-400/10' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-lg dark:border-slate-800 dark:bg-slate-900'}`}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-lg font-semibold text-slate-900 dark:text-white">{tracker.imei}</p>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${statusBadge(tracker.status)}`}>{tracker.status.replace(/_/g, ' ')}</span>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${integrationBadge(tracker.integration_status)}`}>{tracker.integration_status || 'sem sync'}</span>
                      </div>
                      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{[tracker.brand, tracker.model].filter(Boolean).join(' • ') || 'Sem marca/modelo'}</p>
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{tracker.client_name || 'Sem cliente'} • {tracker.vehicle_plate ? `Veículo ${tracker.vehicle_plate}` : 'Sem veículo'}</p>
                      {tracker.active_plan_name && <p className="mt-1 text-xs font-medium text-brand-600 dark:text-cyan-400">{tracker.active_plan_name}</p>}
                    </div>
                    {canEdit ? <Button type="button" onClick={(event) => { event.stopPropagation(); openEditModal(tracker); }}>Editar</Button> : null}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Detalhes" title={selectedTracker ? selectedTracker.imei || 'Rastreador selecionado' : 'Selecione um rastreador'} description={selectedTracker ? 'Resumo do vínculo técnico, identificação do cliente e situação do equipamento.' : 'Escolha um rastreador para consultar o detalhamento.'} actions={selectedTracker && canEdit ? <button type="button" onClick={deleteTracker} className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm font-semibold text-rose-700">Excluir</button> : null} />
            {selectedTracker ? (
              <div className="mt-5 space-y-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Cliente</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedTracker.client_name || 'Não vinculado'}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{selectedTracker.client_cpf_cnpj ? formatCpfCnpj(selectedTracker.client_cpf_cnpj) : 'Sem documento'}</p></div>
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Veículo</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedTracker.vehicle_plate || 'Sem vínculo'}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{selectedTracker.integration_last_code || '-'} • {selectedTracker.integration_last_description || 'Sem retorno externo'}</p></div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Fabricante</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedTracker.external_manufacturer_label || selectedTracker.brand || 'Não informado'}</p></div>
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">SIM / ICCID</p><p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{selectedTracker.sim_number || 'Sem linha'} • {selectedTracker.sim_iccid || 'Sem ICCID'}</p></div>
                </div>
                <div className={`rounded-2xl border p-4 ${trackerContract ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40' : 'border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/60'}`}>
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Plano contratado</p>
                  {trackerContract ? (
                    <>
                      <p className="mt-2 font-semibold text-slate-900 dark:text-white">{trackerContract.plan_name || 'Plano ativo'}</p>
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                        {trackerContract.monthly_value != null ? `R$ ${trackerContract.monthly_value.toFixed(2)}/mês` : ''}{trackerContract.next_due_date ? ` • Próx. venc: ${trackerContract.next_due_date}` : ''}
                      </p>
                    </>
                  ) : (
                    <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{selectedTracker.vehicle_id ? 'Nenhum contrato ativo — cadastre em Contratos' : 'Vincule a um veículo para ver o plano'}</p>
                  )}
                </div>
                {vehicleTrackers.length > 0 && (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                    <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Outros equipamentos neste veículo</p>
                    <ul className="mt-3 space-y-2">
                      {vehicleTrackers.map((t) => (
                        <li
                          key={t.id}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelectedTracker(t)}
                          onKeyDown={(e) => cardKeyHandler(e, () => setSelectedTracker(t))}
                          className="flex cursor-pointer items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm transition hover:border-brand-400 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-cyan-500"
                        >
                          <span className="font-medium text-slate-800 dark:text-white">{t.imei}</span>
                          <span className="text-xs text-slate-500 dark:text-slate-400">{t.active_plan_name || 'sem plano'}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : null}
          </Card>

          <Card>
            <SectionHeader eyebrow="Histórico" title="Linha do tempo do equipamento" description="Movimentações de vínculo e alterações relevantes do rastreador selecionado." />
            <div className="mt-5 space-y-3">
              {!selectedTracker ? <p className="text-sm text-slate-500 dark:text-slate-400">Selecione um rastreador para consultar o histórico.</p> : history.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum evento registrado até o momento.</p> : history.map((entry) => (
                <div key={entry.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                  <div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-slate-900 dark:text-white">{friendlyAction(entry.action)}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{entry.created_at ? new Date(entry.created_at).toLocaleString('pt-BR') : entry.event_date || '-'}</p></div><span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold uppercase text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">{entry.new_status || '-'}</span></div>
                  {entry.notes ? <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">{entry.notes}</p> : null}
                </div>
              ))}
            </div>
          </Card>
        </div>
      </section>

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
              <select className={fieldClass} value={form.vehicle_id} onChange={(e) => setForm((prev) => ({ ...prev, vehicle_id: e.target.value }))}><option value="">Sem veículo</option>{filteredVehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.plate} {vehicle.model ? `• ${vehicle.model}` : ''}</option>)}</select>
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
                <input type="date" className={fieldClass} value={form.link_start_date} onChange={(e) => setForm((prev) => ({ ...prev, link_start_date: e.target.value }))} title="Início do contrato" />
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
                    <div className="grid grid-cols-2 gap-3">
                      <input className={fieldClass} placeholder="Dia venc. (1-28)" type="number" min={1} max={28} value={form.link_billing_day} onChange={(e) => setForm((prev) => ({ ...prev, link_billing_day: e.target.value }))} />
                      <input className={fieldClass} placeholder="Ciclos (meses)" type="number" min={1} max={60} value={form.link_billing_cycles} onChange={(e) => setForm((prev) => ({ ...prev, link_billing_cycles: e.target.value }))} />
                    </div>
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
            <input type="date" className={fieldClass} value={form.acquisition_date} onChange={(e) => setForm((prev) => ({ ...prev, acquisition_date: e.target.value }))} />
            <input type="date" className={fieldClass} value={form.install_date} onChange={(e) => setForm((prev) => ({ ...prev, install_date: e.target.value }))} />
            <input type="date" className={fieldClass} value={form.warranty_until} onChange={(e) => setForm((prev) => ({ ...prev, warranty_until: e.target.value }))} />
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
