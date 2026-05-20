'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { apiFetch } from '@/lib/api';
import { fetchAddressByCep } from '@/lib/cep';
import { formatZipCode, onlyDigits } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';

type ClientOption = {
  id: number;
  name: string;
  cpf_cnpj: string;
  address_zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
};

type ServiceProductOption = { id: number; name: string; auto_add_on_uninstall?: boolean };
type ContractOption = { id: number; client_id: number; plan_name?: string | null; status: string };
type VehicleStatus = 'pendente_validacao' | 'em_analise' | 'aprovado' | 'reprovado' | 'correcao_solicitada' | 'ativo' | 'sem_rastreador' | 'retirado' | 'bloqueado';

type Vehicle = {
  id: number;
  client_id: number;
  sales_point?: string | null;
  seller_consultant?: string | null;
  vehicle_classification?: string | null;
  user_alert?: string | null;
  contract_number?: string | null;
  contract_date?: string | null;
  contract_end_date?: string | null;
  address_zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
  plate: string;
  chassis?: string | null;
  renavam?: string | null;
  brand?: string | null;
  model?: string | null;
  manufacture_year?: number | null;
  model_year?: number | null;
  color?: string | null;
  fuel_type?: string | null;
  type?: string | null;
  status: VehicleStatus;
};

type VehicleDocument = {
  id: number;
  file_name: string;
  category: string;
  review_status?: string;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

type VehicleFormState = {
  client_id: string;
  sales_point: string;
  seller_consultant: string;
  vehicle_classification: string;
  user_alert: string;
  contract_number: string;
  contract_date: string;
  contract_end_date: string;
  address_zip_code: string;
  address_line: string;
  address_number: string;
  address_complement: string;
  neighborhood: string;
  city: string;
  state: string;
  plate: string;
  chassis: string;
  renavam: string;
  brand: string;
  model: string;
  manufacture_year: string;
  model_year: string;
  color: string;
  fuel_type: string;
  type: string;
  status: VehicleStatus;
};

const initialForm: VehicleFormState = {
  client_id: '',
  sales_point: 'MASTERSAT RASTREAMENTO',
  seller_consultant: '',
  vehicle_classification: 'NAO INFORMADO',
  user_alert: 'Nenhum',
  contract_number: '',
  contract_date: '',
  contract_end_date: '',
  address_zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
  plate: '',
  chassis: '',
  renavam: '',
  brand: '',
  model: '',
  manufacture_year: '',
  model_year: '',
  color: '',
  fuel_type: '',
  type: '',
  status: 'ativo',
};

const fieldClass = 'w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400';
const areaClass = `${fieldClass} min-h-[96px] resize-y`;
const salesPointOptions = ['MASTERSAT RASTREAMENTO'];
const classificationOptions = ['NAO INFORMADO', 'LEVE', 'UTILITARIO', 'PESADO'];
const alertOptions = ['Nenhum', 'Alerta de inadimplência', 'Pendência documental', 'Atenção operacional'];
const typeOptions = ['carro', 'moto', 'caminhao', 'outros'];
const fuelOptions = ['gasolina', 'etanol', 'flex', 'diesel', 'gnv', 'eletrico', 'hibrido', 'outro'];
const colorOptions = ['preto', 'branco', 'prata', 'cinza', 'azul', 'vermelho', 'verde', 'amarelo', 'marrom', 'bege', 'outro'];
const documentCategoryOptions = ['documento_veiculo', 'crlv', 'foto_frontal', 'foto_lateral', 'foto_traseira', 'comprovante_propriedade', 'outro'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}
function formatPlate(value: string) { return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7); }
function formatRenavam(value: string) { return value.replace(/\D/g, '').slice(0, 11); }
function formatChassis(value: string) { return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17); }

function statusBadge(status: string) {
  switch (status) {
    case 'ativo':
    case 'aprovado':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'retirado':
    case 'bloqueado':
      return 'border-rose-200 bg-rose-50 text-rose-700';
    case 'sem_rastreador':
    case 'correcao_solicitada':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'em_analise':
      return 'border-cyan-200 bg-cyan-50 text-cyan-700';
    default:
      return 'border-slate-200 bg-slate-50 text-slate-700';
  }
}


function cardKeyHandler(event: React.KeyboardEvent<HTMLElement>, callback: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    callback();
  }
}

export default function VeiculosPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [contracts, setContracts] = useState<ContractOption[]>([]);
  const [serviceProducts, setServiceProducts] = useState<ServiceProductOption[]>([]);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [vehicleDocuments, setVehicleDocuments] = useState<VehicleDocument[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [form, setForm] = useState<VehicleFormState>(initialForm);
  const [modalOpen, setModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [docCategory, setDocCategory] = useState('crlv');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uninstallOpen, setUninstallOpen] = useState(false);
  const [uninstallDate, setUninstallDate] = useState(new Date().toISOString().slice(0, 10));
  const [destinationContractId, setDestinationContractId] = useState('');
  const [uninstallServiceProductId, setUninstallServiceProductId] = useState('');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');

  async function loadVehicles(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      query.set('limit', '300');

      const [vehicleResponse, clientResponse, contractResponse, serviceProductResponse] = await Promise.all([
        apiFetch<Vehicle[]>(`/vehicles?${query.toString()}`, {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=300', {}, currentToken),
        apiFetch<ContractOption[]>('/contracts', {}, currentToken).catch(() => []),
        apiFetch<ServiceProductOption[]>('/service-products', {}, currentToken).catch(() => []),
      ]);
      setVehicles(vehicleResponse);
      setClients(clientResponse);
      setContracts(contractResponse.filter((item) => item.status === 'ativo'));
      setServiceProducts(serviceProductResponse);
      if (selectedVehicle) setSelectedVehicle(vehicleResponse.find((item) => item.id === selectedVehicle.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadDocuments(currentToken: string, vehicleId: number) {
    try {
      const response = await apiFetch<VehicleDocument[]>(`/vehicles/${vehicleId}/documents`, {}, currentToken);
      setVehicleDocuments(response);
    } catch (err) {
      setError(parseError(err));
    }
  }

  useEffect(() => {
    if (!token) return;
    loadVehicles(token);
  }, [token]);

  useEffect(() => {
    if (!token || !selectedVehicle) {
      setVehicleDocuments([]);
      return;
    }
    loadDocuments(token, selectedVehicle.id);
  }, [token, selectedVehicle?.id]);

  const stats = useMemo(() => ({
    total: vehicles.length,
    active: vehicles.filter((item) => item.status === 'ativo').length,
    withoutTracker: vehicles.filter((item) => item.status === 'sem_rastreador').length,
    removed: vehicles.filter((item) => item.status === 'retirado').length,
  }), [vehicles]);

  function resetForm() {
    setForm(initialForm);
    setDocFiles([]);
    setDocCategory('crlv');
    setIsEditing(false);
  }

  function populateFromClient(clientId: string, useAddress = false) {
    const selectedClient = clients.find((item) => item.id === Number(clientId));
    if (!selectedClient || !useAddress) return;
    setForm((prev) => ({
      ...prev,
      address_zip_code: selectedClient.address_zip_code ? formatZipCode(selectedClient.address_zip_code) : prev.address_zip_code,
      address_line: selectedClient.address_line || prev.address_line,
      address_number: selectedClient.address_number || prev.address_number,
      address_complement: selectedClient.address_complement || prev.address_complement,
      neighborhood: selectedClient.neighborhood || prev.neighborhood,
      city: selectedClient.city || prev.city,
      state: selectedClient.state || prev.state,
    }));
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setModalError('');
    setIsEditing(true);
    setForm({
      client_id: String(vehicle.client_id || ''),
      sales_point: vehicle.sales_point || 'MASTERSAT RASTREAMENTO',
      seller_consultant: vehicle.seller_consultant || '',
      vehicle_classification: vehicle.vehicle_classification || 'NAO INFORMADO',
      user_alert: vehicle.user_alert || 'Nenhum',
      contract_number: vehicle.contract_number || '',
      contract_date: vehicle.contract_date || '',
      contract_end_date: vehicle.contract_end_date || '',
      address_zip_code: vehicle.address_zip_code ? formatZipCode(vehicle.address_zip_code) : '',
      address_line: vehicle.address_line || '',
      address_number: vehicle.address_number || '',
      address_complement: vehicle.address_complement || '',
      neighborhood: vehicle.neighborhood || '',
      city: vehicle.city || '',
      state: vehicle.state || '',
      plate: vehicle.plate || '',
      chassis: vehicle.chassis || '',
      renavam: vehicle.renavam || '',
      brand: vehicle.brand || '',
      model: vehicle.model || '',
      manufacture_year: vehicle.manufacture_year ? String(vehicle.manufacture_year) : '',
      model_year: vehicle.model_year ? String(vehicle.model_year) : '',
      color: vehicle.color || '',
      fuel_type: vehicle.fuel_type || '',
      type: vehicle.type || '',
      status: vehicle.status,
    });
    setDocFiles([]);
    setModalOpen(true);
  }

  async function fillAddressByCep(value: string) {
    const cep = onlyDigits(value);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    try {
      const address = await fetchAddressByCep(cep);
      if (address) {
        setForm((prev) => ({
          ...prev,
          address_zip_code: formatZipCode(cep),
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

  async function submitVehicle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setModalError('');
    setFeedback('');
    try {
      const payload = {
        client_id: Number(form.client_id),
        sales_point: form.sales_point || null,
        seller_consultant: form.seller_consultant || null,
        vehicle_classification: form.vehicle_classification || null,
        user_alert: form.user_alert || null,
        contract_number: form.contract_number || null,
        contract_date: form.contract_date || null,
        contract_end_date: form.contract_end_date || null,
        address_zip_code: onlyDigits(form.address_zip_code) || null,
        address_line: form.address_line || null,
        address_number: form.address_number || null,
        address_complement: form.address_complement || null,
        neighborhood: form.neighborhood || null,
        city: form.city || null,
        state: form.state || null,
        plate: formatPlate(form.plate),
        chassis: formatChassis(form.chassis) || null,
        renavam: formatRenavam(form.renavam) || null,
        brand: form.brand || null,
        model: form.model || null,
        manufacture_year: form.manufacture_year ? Number(form.manufacture_year) : null,
        model_year: form.model_year ? Number(form.model_year) : null,
        color: form.color || null,
        fuel_type: form.fuel_type || null,
        type: form.type || null,
        status: form.status,
      };

      const saved = isEditing && selectedVehicle
        ? await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<Vehicle>('/vehicles', { method: 'POST', body: JSON.stringify(payload) }, token);

      if (docFiles.length) {
        const body = new FormData();
        body.append('category', docCategory);
        docFiles.forEach((file) => body.append('files', file));
        await apiFetch(`/vehicles/${saved.id}/documents`, { method: 'POST', body }, token);
      }

      setFeedback(isEditing ? 'Veículo atualizado com sucesso.' : 'Veículo cadastrado com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadVehicles(token);
      setSelectedVehicle(saved);
      await loadDocuments(token, saved.id);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function uploadDocuments() {
    if (!token || !selectedVehicle || !canEdit || !docFiles.length) return;
    setUploading(true);
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents`, { method: 'POST', body }, token);
      await loadDocuments(token, selectedVehicle.id);
      setDocFiles([]);
      setFeedback('Documentos enviados com sucesso.');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function removeDocument(documentId: number) {
    if (!token || !selectedVehicle || !canEdit) return;
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      await loadDocuments(token, selectedVehicle.id);
      setFeedback('Documento removido com sucesso.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function reviewDocument(documentId: number, reviewStatus: string) {
    if (!token || !selectedVehicle || !canEdit) return;
    const reviewNotes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents/${documentId}/review`, {
        method: 'POST',
        body: JSON.stringify({ review_status: reviewStatus, review_notes: reviewNotes || null }),
      }, token);
      await loadDocuments(token, selectedVehicle.id);
      setFeedback('Status do documento atualizado.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function deleteVehicle(vehicleId: number) {
    if (!token || !canEdit) return;
    if (!window.confirm('Deseja remover este veículo?')) return;
    try {
      await apiFetch(`/vehicles/${vehicleId}`, { method: 'DELETE' }, token);
      setFeedback('Veículo removido com sucesso.');
      if (selectedVehicle?.id === vehicleId) setSelectedVehicle(null);
      await loadVehicles(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function confirmUninstall() {
    if (!token || !selectedVehicle || !canEdit) return;
    setModalError('');
    try {
      const params = new URLSearchParams();
      if (uninstallDate) params.set('uninstall_date', uninstallDate);
      if (destinationContractId) params.set('destination_contract_id', destinationContractId);
      if (uninstallServiceProductId) params.set('uninstall_service_product_id', uninstallServiceProductId);
      await apiFetch(`/vehicles/${selectedVehicle.id}/uninstall?${params.toString()}`, { method: 'POST' }, token);
      setFeedback('Desinstalação registrada com sucesso.');
      setUninstallOpen(false);
      await loadVehicles(token);
      const updated = await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, {}, token);
      setSelectedVehicle(updated);
    } catch (err) {
      setModalError(parseError(err));
    }
  }

  return (
    <PageShell title="Veículos" description="Gestão dos ativos da frota com cadastro em modal, documentação, desinstalação e visão consolidada por cliente.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Veículos cadastrados" value={stats.total} hint="Base total de ativos" icon="🚗" />
        <StatCard label="Em operação" value={stats.active} hint="Status ativo/aprovado" tone="success" icon="✅" />
        <StatCard label="Sem rastreador" value={stats.withoutTracker} hint="Exigem ação técnica" tone="warning" icon="📍" />
        <StatCard label="Retirados" value={stats.removed} hint="Veículos desinstalados" tone="danger" icon="↩️" />
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Cadastro" title="Base de veículos" description="Use filtros para localizar ativos rapidamente e mantenha o cadastro técnico limpo no modal." actions={canEdit ? <Button type="button" onClick={openCreateModal}>Adicionar veículo</Button> : null} />
            <div className="mt-5 grid gap-4 md:grid-cols-4">
              <input className={fieldClass} placeholder="Buscar por placa, chassi, modelo ou Renavam" value={search} onChange={(e) => setSearch(e.target.value)} />
              <select className={fieldClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">Todos os status</option>
                <option value="ativo">Ativo</option><option value="sem_rastreador">Sem rastreador</option><option value="retirado">Retirado</option><option value="bloqueado">Bloqueado</option>
              </select>
              <select className={fieldClass} value={clientFilter} onChange={(e) => setClientFilter(e.target.value)}>
                <option value="">Todos os clientes</option>
                {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
              </select>
              <Button type="button" onClick={() => token && loadVehicles(token)} disabled={loading}>{loading ? 'Atualizando...' : 'Aplicar filtros'}</Button>
            </div>
            <div className="mt-5 grid gap-3">
              {vehicles.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-6 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhum veículo encontrado.</p> : vehicles.map((vehicle) => {
                const client = clients.find((item) => item.id === vehicle.client_id);
                return (
                  <div key={vehicle.id} role="button" tabIndex={0} onClick={() => setSelectedVehicle(vehicle)} onKeyDown={(event) => cardKeyHandler(event, () => setSelectedVehicle(vehicle))} className={`cursor-pointer rounded-[24px] border p-5 text-left transition ${selectedVehicle?.id === vehicle.id ? 'border-brand-500 bg-brand-50/60 shadow-sm dark:border-cyan-400 dark:bg-cyan-400/10' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-lg dark:border-slate-800 dark:bg-slate-900'}`}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-lg font-semibold text-slate-900 dark:text-white">{vehicle.plate}</p>
                          <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${statusBadge(vehicle.status)}`}>{vehicle.status.replace(/_/g, ' ')}</span>
                        </div>
                        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{[vehicle.brand, vehicle.model].filter(Boolean).join(' • ') || 'Modelo não informado'} {vehicle.model_year ? `• ${vehicle.model_year}` : ''}</p>
                        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Cliente: {client?.name || 'não identificado'} • Chassi: {vehicle.chassis || 'não informado'}</p>
                      </div>
                      {canEdit ? <div className="flex gap-2"><Button type="button" onClick={(event) => { event.stopPropagation(); openEditModal(vehicle); }}>Editar</Button><button type="button" onClick={(event) => { event.stopPropagation(); deleteVehicle(vehicle.id); }} className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm font-semibold text-rose-700">Excluir</button></div> : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Detalhes" title={selectedVehicle ? selectedVehicle.plate : 'Selecione um veículo'} description={selectedVehicle ? 'Veja dados cadastrais, documentos e ações operacionais.' : 'Escolha um veículo na lista para detalhar a gestão do ativo.'} actions={selectedVehicle && canEdit ? <Button type="button" onClick={() => setUninstallOpen(true)}>Gerar desinstalação</Button> : null} />
            {selectedVehicle ? (
              <div className="mt-5 space-y-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Cliente</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{clients.find((item) => item.id === selectedVehicle.client_id)?.name || 'Não encontrado'}</p></div>
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Contrato</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedVehicle.contract_number || 'Não informado'}</p></div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Chassi / Renavam</p><p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{selectedVehicle.chassis || '—'} • {selectedVehicle.renavam || '—'}</p></div>
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Endereço de atendimento</p><p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{[selectedVehicle.address_line, selectedVehicle.address_number, selectedVehicle.city, selectedVehicle.state].filter(Boolean).join(' • ') || 'Não informado'}</p></div>
                </div>
              </div>
            ) : null}
          </Card>

          <Card>
            <SectionHeader eyebrow="Documentação" title="Documentos do veículo" description="Centralize CRLV, fotos e comprovantes do ativo em um único lugar." />
            {selectedVehicle ? (
              <>
                <div className="mt-5 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                  <select className={fieldClass} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>{documentCategoryOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
                  <input type="file" multiple className={`${fieldClass} file:mr-4 file:rounded-xl file:border-0 file:bg-slate-950 file:px-3 file:py-2 file:text-white dark:file:bg-cyan-400 dark:file:text-slate-950`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                  <Button type="button" disabled={!canEdit || !docFiles.length || uploading} onClick={uploadDocuments}>{uploading ? 'Enviando...' : 'Enviar'}</Button>
                </div>
                <div className="mt-5 space-y-3">
                  {vehicleDocuments.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum documento vinculado.</p> : vehicleDocuments.map((document) => (
                    <div key={document.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900 dark:text-white">{document.file_name}</p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Categoria: {document.category}</p>
                          {document.review_notes ? <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Obs.: {document.review_notes}</p> : null}
                        </div>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${statusBadge(document.review_status || 'enviado')}`}>{(document.review_status || 'enviado').replace(/_/g, ' ')}</span>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <a href={document.url} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Visualizar</a>
                        <a href={document.download_url} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Baixar</a>
                        {canEdit ? <><button type="button" onClick={() => reviewDocument(document.id, 'aprovado')} className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700">Aprovar</button><button type="button" onClick={() => reviewDocument(document.id, 'reenvio_solicitado')} className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-700">Solicitar ajuste</button><button type="button" onClick={() => removeDocument(document.id)} className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700">Excluir</button></> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">Selecione um veículo para ver os documentos.</p>}
          </Card>
        </div>
      </section>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar veículo' : 'Novo veículo'} description="Mantenha o cadastro técnico e documental do veículo em um fluxo de preenchimento mais limpo." size="2xl">
        <form className="space-y-6" onSubmit={submitVehicle}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-3">
            <select className={fieldClass} value={form.client_id} onChange={(e) => setForm((prev) => ({ ...prev, client_id: e.target.value }))} required>
              <option value="">Selecione o cliente</option>
              {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
            </select>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-200"><input type="checkbox" onChange={(e) => populateFromClient(form.client_id, e.target.checked)} /> Usar endereço do cliente</label>
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as VehicleStatus }))}>{['ativo','sem_rastreador','retirado','bloqueado','pendente_validacao','em_analise','aprovado','reprovado','correcao_solicitada'].map((option) => <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>)}</select>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <select className={fieldClass} value={form.sales_point} onChange={(e) => setForm((prev) => ({ ...prev, sales_point: e.target.value }))}>{salesPointOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.vehicle_classification} onChange={(e) => setForm((prev) => ({ ...prev, vehicle_classification: e.target.value }))}>{classificationOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.user_alert} onChange={(e) => setForm((prev) => ({ ...prev, user_alert: e.target.value }))}>{alertOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <input className={fieldClass} placeholder="Consultor / responsável" value={form.seller_consultant} onChange={(e) => setForm((prev) => ({ ...prev, seller_consultant: e.target.value }))} />
            <input className={fieldClass} placeholder="Número do contrato" value={form.contract_number} onChange={(e) => setForm((prev) => ({ ...prev, contract_number: e.target.value }))} />
            <div className="grid grid-cols-2 gap-4"><input type="date" className={fieldClass} value={form.contract_date} onChange={(e) => setForm((prev) => ({ ...prev, contract_date: e.target.value }))} /><input type="date" className={fieldClass} value={form.contract_end_date} onChange={(e) => setForm((prev) => ({ ...prev, contract_end_date: e.target.value }))} /></div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <input className={fieldClass} placeholder="Placa" value={form.plate} onChange={(e) => setForm((prev) => ({ ...prev, plate: formatPlate(e.target.value) }))} required />
            <input className={fieldClass} placeholder="Chassi" value={form.chassis} onChange={(e) => setForm((prev) => ({ ...prev, chassis: formatChassis(e.target.value) }))} />
            <input className={fieldClass} placeholder="Renavam" value={form.renavam} onChange={(e) => setForm((prev) => ({ ...prev, renavam: formatRenavam(e.target.value) }))} />
            <input className={fieldClass} placeholder="Marca" value={form.brand} onChange={(e) => setForm((prev) => ({ ...prev, brand: e.target.value }))} />
            <input className={fieldClass} placeholder="Modelo" value={form.model} onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))} />
            <div className="grid grid-cols-2 gap-4"><input className={fieldClass} placeholder="Ano fabricação" value={form.manufacture_year} onChange={(e) => setForm((prev) => ({ ...prev, manufacture_year: onlyDigits(e.target.value).slice(0, 4) }))} /><input className={fieldClass} placeholder="Ano modelo" value={form.model_year} onChange={(e) => setForm((prev) => ({ ...prev, model_year: onlyDigits(e.target.value).slice(0, 4) }))} /></div>
            <select className={fieldClass} value={form.type} onChange={(e) => setForm((prev) => ({ ...prev, type: e.target.value }))}><option value="">Tipo de veículo</option>{typeOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.fuel_type} onChange={(e) => setForm((prev) => ({ ...prev, fuel_type: e.target.value }))}><option value="">Combustível</option>{fuelOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.color} onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}><option value="">Cor</option>{colorOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
          </div>
          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <div className="flex gap-2">
                <input className={fieldClass} placeholder="CEP" value={form.address_zip_code} onChange={(e) => setForm((prev) => ({ ...prev, address_zip_code: formatZipCode(e.target.value) }))} onBlur={(e) => fillAddressByCep(e.target.value)} />
                <button type="button" className="rounded-2xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-200" onClick={() => fillAddressByCep(form.address_zip_code)} disabled={lookingUpCep}>Buscar</button>
              </div>
              {lookingUpCep ? <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Buscando endereço...</p> : null}
            </div>
            <input className="md:col-span-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400" placeholder="Logradouro" value={form.address_line} onChange={(e) => setForm((prev) => ({ ...prev, address_line: e.target.value }))} />
            <input className={fieldClass} placeholder="Número" value={form.address_number} onChange={(e) => setForm((prev) => ({ ...prev, address_number: e.target.value }))} />
            <input className={fieldClass} placeholder="Complemento" value={form.address_complement} onChange={(e) => setForm((prev) => ({ ...prev, address_complement: e.target.value }))} />
            <input className={fieldClass} placeholder="Bairro" value={form.neighborhood} onChange={(e) => setForm((prev) => ({ ...prev, neighborhood: e.target.value }))} />
            <input className={fieldClass} placeholder="Cidade" value={form.city} onChange={(e) => setForm((prev) => ({ ...prev, city: e.target.value }))} />
            <input className={fieldClass} placeholder="UF" value={form.state} onChange={(e) => setForm((prev) => ({ ...prev, state: e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))} maxLength={2} />
          </div>
          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Documentação inicial</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Opcional. Se houver arquivos selecionados, eles serão enviados após salvar o veículo.</p>
            <div className="mt-4 grid gap-4 md:grid-cols-[220px_1fr]">
              <select className={fieldClass} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>{documentCategoryOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
              <input type="file" multiple className={`${fieldClass} file:mr-4 file:rounded-xl file:border-0 file:bg-slate-950 file:px-3 file:py-2 file:text-white dark:file:bg-cyan-400 dark:file:text-slate-950`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar veículo' : 'Cadastrar veículo'}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={uninstallOpen} onClose={() => { setUninstallOpen(false); setModalError(''); }} title="Registrar desinstalação" description="Gere a desinstalação do veículo com pró-rata, taxa de serviço e retorno do equipamento ao estoque." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Data da desinstalação</span><input type="date" className={fieldClass} value={uninstallDate} onChange={(e) => setUninstallDate(e.target.value)} /></label>
            <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Taxa de desinstalação</span><select className={fieldClass} value={uninstallServiceProductId} onChange={(e) => setUninstallServiceProductId(e.target.value)}><option value="">Selecione um serviço</option>{serviceProducts.map((item) => <option key={item.id} value={item.id}>{item.name}{item.auto_add_on_uninstall ? ' • recomendado' : ''}</option>)}</select></label>
            <label className="block md:col-span-2"><span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Contrato de destino (opcional)</span><select className={fieldClass} value={destinationContractId} onChange={(e) => setDestinationContractId(e.target.value)}><option value="">Sem transferência</option>{contracts.map((item) => <option key={item.id} value={item.id}>{item.client_id} • {item.plan_name || 'Contrato'}</option>)}</select></label>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => setUninstallOpen(false)}>Cancelar</button>
            <Button type="button" onClick={confirmUninstall}>Confirmar desinstalação</Button>
          </div>
        </div>
      </Modal>
    </PageShell>
  );
}
