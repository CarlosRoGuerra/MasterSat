'use client';

import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { fetchAddressByCep } from '@/lib/cep';
import { formatZipCode, onlyDigits } from '@/lib/format';

type ClientType = 'pf' | 'pj';

type ClientOption = {
  id: number;
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  email?: string | null;
  extra_emails?: string[] | null;
  phone?: string | null;
  zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
};

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
  year?: number | null;
  manufacture_year?: number | null;
  model_year?: number | null;
  color?: string | null;
  fuel_type?: string | null;
  type?: string | null;
  fipe_code?: string | null;
  fipe_value?: number | null;
  status: VehicleStatus;
};

type VehicleDocument = {
  id: number;
  file_name: string;
  category: string;
  content_type: string;
  size_bytes: number;
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
  fipe_code: string;
  fipe_value: string;
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
  fipe_code: '',
  fipe_value: '',
  status: 'ativo',
};

const statusOptions: VehicleStatus[] = ['ativo', 'pendente_validacao', 'em_analise', 'aprovado', 'reprovado', 'correcao_solicitada', 'sem_rastreador', 'retirado', 'bloqueado'];
const salesPointOptions = ['MASTERSAT RASTREAMENTO'];
const classificationOptions = ['NAO INFORMADO', 'LEVE', 'UTILITARIO', 'PESADO'];
const alertOptions = ['Nenhum', 'Alerta de inadimplência', 'Pendência documental', 'Atenção operacional'];
const typeOptions = ['carro', 'moto', 'caminhao', 'outros'];
const fuelOptions = ['gasolina', 'etanol', 'flex', 'diesel', 'gnv', 'eletrico', 'hibrido', 'outro'];
const colorOptions = ['preto', 'branco', 'prata', 'cinza', 'azul', 'vermelho', 'verde', 'amarelo', 'marrom', 'bege', 'outro'];
const documentCategoryOptions = ['documento_veiculo', 'crlv', 'foto_frontal', 'foto_lateral', 'foto_traseira', 'comprovante_propriedade', 'outro'];
const stateOptions = ['AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT', 'PA', 'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO'];

function formatPlate(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7);
}

function formatRenavam(value: string) {
  return value.replace(/\D/g, '').slice(0, 11);
}

function formatChassis(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17);
}

function formatMoneyInput(value: string) {
  return value.replace(/[^\d,\.]/g, '');
}

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function normalizeDate(value: string) {
  return value || null;
}

function parseMoney(value: string) {
  if (!value) return null;
  const normalized = value.replace(/\./g, '').replace(',', '.');
  const amount = Number(normalized);
  return Number.isFinite(amount) ? amount : null;
}

function VehicleField({ label, required, children }: { label: string; required?: boolean; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium text-slate-700">{label}{required ? <span className="text-red-500"> • obrigatório</span> : null}</span>
      {children}
    </label>
  );
}

export default function VehiclesPage() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [documents, setDocuments] = useState<VehicleDocument[]>([]);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [form, setForm] = useState<VehicleFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadCategory, setUploadCategory] = useState('documento_veiculo');
  const [uploading, setUploading] = useState(false);
  const [useClientAddress, setUseClientAddress] = useState(true);
  const [lookingUpCep, setLookingUpCep] = useState(false);

  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');

  async function loadBaseData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search) query.set('search', search);
      if (statusFilter) query.set('status', statusFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      if (typeFilter) query.set('type', typeFilter);
      query.set('limit', '100');

      const [vehicleResponse, clientResponse] = await Promise.all([
        apiFetch<Vehicle[]>(`/vehicles?${query.toString()}`, {}, currentToken),
        apiFetch<ClientOption[]>('/clients', {}, currentToken),
      ]);
      setVehicles(vehicleResponse);
      setClients(clientResponse);
    } catch (err) {
      const message = parseError(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadBaseData(token);
  }, [token]);

  async function loadDocuments(vehicleId: number, currentToken: string) {
    try {
      const response = await apiFetch<VehicleDocument[]>(`/vehicles/${vehicleId}/documents`, {}, currentToken);
      setDocuments(response);
    } catch (err) {
      setError(parseError(err));
    }
  }

  function resetForm() {
    setForm(initialForm);
    setSelectedVehicle(null);
    setDocuments([]);
    setUploadFiles([]);
    setUploadCategory('documento_veiculo');
    setUseClientAddress(true);
    setIsEditing(false);
  }

  function populateAddressFromClient(clientId: string) {
    const client = clients.find((item) => item.id === Number(clientId));
    if (!client) return;
    setForm((prev) => ({
      ...prev,
      client_id: clientId,
      address_zip_code: client.zip_code ? formatZipCode(client.zip_code) : prev.address_zip_code,
      address_line: client.address_line || prev.address_line,
      address_number: client.address_number || prev.address_number,
      address_complement: client.address_complement || prev.address_complement,
      neighborhood: client.neighborhood || prev.neighborhood,
      city: client.city || prev.city,
      state: client.state || prev.state,
    }));
  }

  function handleEdit(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setForm({
      client_id: String(vehicle.client_id),
      sales_point: vehicle.sales_point ?? 'MASTERSAT RASTREAMENTO',
      seller_consultant: vehicle.seller_consultant ?? '',
      vehicle_classification: vehicle.vehicle_classification ?? 'NAO INFORMADO',
      user_alert: vehicle.user_alert ?? 'Nenhum',
      contract_number: vehicle.contract_number ?? '',
      contract_date: vehicle.contract_date ?? '',
      contract_end_date: vehicle.contract_end_date ?? '',
      address_zip_code: vehicle.address_zip_code ? formatZipCode(vehicle.address_zip_code) : '',
      address_line: vehicle.address_line ?? '',
      address_number: vehicle.address_number ?? '',
      address_complement: vehicle.address_complement ?? '',
      neighborhood: vehicle.neighborhood ?? '',
      city: vehicle.city ?? '',
      state: vehicle.state ?? '',
      plate: vehicle.plate,
      chassis: vehicle.chassis ?? '',
      renavam: vehicle.renavam ?? '',
      brand: vehicle.brand ?? '',
      model: vehicle.model ?? '',
      manufacture_year: vehicle.manufacture_year ? String(vehicle.manufacture_year) : '',
      model_year: vehicle.model_year ? String(vehicle.model_year) : vehicle.year ? String(vehicle.year) : '',
      color: vehicle.color ?? '',
      fuel_type: vehicle.fuel_type ?? '',
      type: vehicle.type ?? '',
      fipe_code: vehicle.fipe_code ?? '',
      fipe_value: vehicle.fipe_value != null ? String(vehicle.fipe_value).replace('.', ',') : '',
      status: vehicle.status,
    });
    setUseClientAddress(false);
    setIsEditing(true);
    if (token) loadDocuments(vehicle.id, token);
  }

  function handleInputChange(field: keyof VehicleFormState, value: string) {
    let nextValue = value;
    if (field === 'plate') nextValue = formatPlate(value);
    if (field === 'renavam') nextValue = formatRenavam(value);
    if (field === 'chassis') nextValue = formatChassis(value);
    if (field === 'address_zip_code') nextValue = formatZipCode(value);
    if (field === 'state') nextValue = value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
    if (field === 'fipe_value') nextValue = formatMoneyInput(value);
    setForm((prev) => ({ ...prev, [field]: nextValue }));
  }

  async function fillAddressFromCep(rawCep: string) {
    const cep = onlyDigits(rawCep);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    setError('');
    try {
      const result = await fetchAddressByCep(cep);
      if (!result) return;
      setForm((prev) => ({
        ...prev,
        address_zip_code: formatZipCode(result.zip_code),
        address_line: result.address_line || prev.address_line,
        address_complement: result.address_complement || prev.address_complement,
        neighborhood: result.neighborhood || prev.neighborhood,
        city: result.city || prev.city,
        state: result.state || prev.state,
      }));
      setFeedback('Endereço preenchido automaticamente pelo CEP.');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLookingUpCep(false);
    }
  }

  async function uploadVehicleFiles(vehicleId: number) {
    if (!token || uploadFiles.length === 0) return;
    const formData = new FormData();
    formData.append('category', uploadCategory);
    uploadFiles.forEach((file) => formData.append('files', file));
    await apiFetch<VehicleDocument[]>(`/vehicles/${vehicleId}/documents`, {
      method: 'POST',
      body: formData,
    }, token);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setSaving(true);
    setFeedback('');
    setError('');
    try {
      const payload = {
        client_id: Number(form.client_id),
        sales_point: form.sales_point || null,
        seller_consultant: form.seller_consultant.trim() || null,
        vehicle_classification: form.vehicle_classification || null,
        user_alert: form.user_alert || null,
        contract_number: form.contract_number.trim() || null,
        contract_date: normalizeDate(form.contract_date),
        contract_end_date: normalizeDate(form.contract_end_date),
        address_zip_code: form.address_zip_code ? onlyDigits(form.address_zip_code) : null,
        address_line: form.address_line.trim() || null,
        address_number: form.address_number.trim() || null,
        address_complement: form.address_complement.trim() || null,
        neighborhood: form.neighborhood.trim() || null,
        city: form.city.trim() || null,
        state: form.state.trim().toUpperCase() || null,
        plate: formatPlate(form.plate),
        chassis: form.chassis ? formatChassis(form.chassis) : null,
        renavam: form.renavam ? formatRenavam(form.renavam) : null,
        brand: form.brand.trim() || null,
        model: form.model.trim() || null,
        manufacture_year: form.manufacture_year ? Number(form.manufacture_year) : null,
        model_year: form.model_year ? Number(form.model_year) : null,
        color: form.color || null,
        fuel_type: form.fuel_type || null,
        type: form.type || null,
        fipe_code: form.fipe_code.trim() || null,
        fipe_value: parseMoney(form.fipe_value),
        status: form.status,
      };

      if (!payload.client_id) throw new Error('Selecione o cliente vinculado ao veículo.');
      if (!payload.plate) throw new Error('Informe a placa do veículo.');
      if (!payload.model) throw new Error('Informe o modelo do veículo.');
      if (!payload.brand) throw new Error('Informe a montadora.');
      if (!payload.type) throw new Error('Selecione o tipo do veículo.');
      if (!payload.fuel_type) throw new Error('Selecione o combustível.');
      if (!payload.color) throw new Error('Selecione a cor.');
      if (!payload.address_zip_code || payload.address_zip_code.length !== 8) throw new Error('Informe um CEP válido para o veículo.');
      if (!payload.address_line || !payload.city || !payload.state || !payload.address_number || !payload.neighborhood) {
        throw new Error('Preencha o endereço completo do veículo.');
      }

      let vehicleId = selectedVehicle?.id;
      if (isEditing && selectedVehicle) {
        const updated = await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        }, token);
        vehicleId = updated.id;
        setFeedback('Veículo atualizado com sucesso.');
      } else {
        const created = await apiFetch<Vehicle>('/vehicles', {
          method: 'POST',
          body: JSON.stringify(payload),
        }, token);
        vehicleId = created.id;
        setFeedback('Veículo cadastrado com sucesso.');
      }

      if (vehicleId && uploadFiles.length > 0) {
        await uploadVehicleFiles(vehicleId);
        setFeedback((current) => `${current} Arquivos enviados com sucesso.`.trim());
      }

      resetForm();
      await loadBaseData(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(vehicleId: number) {
    if (!token) return;
    const confirmed = window.confirm('Deseja realmente excluir este veículo?');
    if (!confirmed) return;
    setError('');
    setFeedback('');
    try {
      await apiFetch<{ message: string }>(`/vehicles/${vehicleId}`, { method: 'DELETE' }, token);
      setFeedback('Veículo removido com sucesso.');
      if (selectedVehicle?.id === vehicleId) resetForm();
      await loadBaseData(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function handleDeleteDocument(documentId: number) {
    if (!token || !selectedVehicle) return;
    if (!window.confirm('Remover este documento?')) return;
    setError('');
    setFeedback('');
    try {
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      setFeedback('Documento removido com sucesso.');
      await loadDocuments(selectedVehicle.id, token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  const selectedClient = useMemo(() => clients.find((client) => client.id === Number(form.client_id)) || null, [clients, form.client_id]);

  return (
    <PageShell title="Veículos">
      {(guardError || error || feedback) && (
        <div className="mb-6 space-y-3">
          {(guardError || error) && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{guardError || error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      {guardLoading && <p className="mb-4 text-sm text-slate-500">Validando sessão...</p>}
      <div className="mb-6 grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <div className="mb-4 grid gap-3 md:grid-cols-[1fr_180px_180px_180px_auto]">
            <VehicleField label="Busca">
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Placa, contrato, modelo, montadora..." className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
            </VehicleField>
            <VehicleField label="Status">
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm"><option value="">Todos</option>{statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
            </VehicleField>
            <VehicleField label="Cliente">
              <select value={clientFilter} onChange={(e) => setClientFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm"><option value="">Todos</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
            </VehicleField>
            <VehicleField label="Tipo">
              <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm"><option value="">Todos</option>{typeOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
            </VehicleField>
            <div className="flex items-end"><Button type="button" className="w-full" onClick={() => loadBaseData(token)}>Atualizar</Button></div>
          </div>

          {loading ? <p className="text-sm text-slate-500">Carregando veículos...</p> : vehicles.length === 0 ? <p className="text-sm text-slate-500">Nenhum veículo encontrado.</p> : (
            <div className="space-y-3">
              {vehicles.map((vehicle) => {
                const owner = clients.find((client) => client.id === vehicle.client_id);
                return (
                  <div key={vehicle.id} className="rounded-2xl border border-slate-200 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-slate-900">{vehicle.plate} • {vehicle.model || 'Sem modelo'}</p>
                        <p className="text-sm text-slate-500">{vehicle.brand || 'Sem montadora'} • {vehicle.status}</p>
                        <p className="text-sm text-slate-500">Cliente: {owner?.name || 'Sem cliente'}{vehicle.contract_number ? ` • Contrato: ${vehicle.contract_number}` : ''}</p>
                      </div>
                      <div className="flex gap-2">
                        <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={() => handleEdit(vehicle)}>Editar</Button>
                        <Button type="button" className="bg-red-600 hover:bg-red-700" onClick={() => handleDelete(vehicle.id)}>Excluir</Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card>
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">{isEditing ? 'Editar veículo' : 'Cadastrar veículo'}</h3>
                <p className="text-sm text-slate-500">Cadastro administrativo seguindo o padrão operacional do formulário.</p>
              </div>
              {isEditing && <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={resetForm}>Novo cadastro</Button>}
            </div>

            <form onSubmit={handleSubmit} className="space-y-6">
              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Cliente vinculado</h4></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <VehicleField label="Cliente" required>
                    <select value={form.client_id} onChange={(e) => { handleInputChange('client_id', e.target.value); if (useClientAddress) populateAddressFromClient(e.target.value); }} className="w-full rounded-xl border border-slate-300 px-4 py-3">
                      <option value="">Selecione um cliente</option>
                      {clients.map((client) => <option key={client.id} value={client.id}>{client.name} • {client.cpf_cnpj}</option>)}
                    </select>
                  </VehicleField>
                  <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-600">
                    <p><strong>E-mail:</strong> {selectedClient?.email || '-'}</p>
                    <p><strong>Telefone:</strong> {selectedClient?.phone || '-'}</p>
                    <p><strong>Tipo:</strong> {selectedClient?.type?.toUpperCase() || '-'}</p>
                  </div>
                </div>
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Ponto de venda</h4></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <VehicleField label="Ponto de venda" required>
                    <select value={form.sales_point} onChange={(e) => handleInputChange('sales_point', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3">{salesPointOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
                  </VehicleField>
                  <VehicleField label="Vendedor / Consultor" required>
                    <input value={form.seller_consultant} onChange={(e) => handleInputChange('seller_consultant', e.target.value)} placeholder="Nome do consultor" className="w-full rounded-xl border border-slate-300 px-4 py-3" />
                  </VehicleField>
                </div>
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Classificação do veículo</h4></div>
                <VehicleField label="Classificação">
                  <select value={form.vehicle_classification} onChange={(e) => handleInputChange('vehicle_classification', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3">{classificationOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
                </VehicleField>
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Alerta</h4></div>
                <VehicleField label="Alerta ao usuário">
                  <select value={form.user_alert} onChange={(e) => handleInputChange('user_alert', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3">{alertOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
                </VehicleField>
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Endereço</h4></div>
                <label className="mb-4 flex items-center gap-3 text-sm text-slate-700"><input type="checkbox" checked={useClientAddress} onChange={(e) => { const checked = e.target.checked; setUseClientAddress(checked); if (checked && form.client_id) populateAddressFromClient(form.client_id); }} /> Utilizar mesmo endereço do cliente</label>
                <div className="grid gap-4 md:grid-cols-2">
                  <VehicleField label="CEP" required><input value={form.address_zip_code} onChange={(e) => handleInputChange('address_zip_code', e.target.value)} onBlur={(e) => fillAddressFromCep(e.target.value)} placeholder="CEP" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Cidade" required><input value={form.city} onChange={(e) => handleInputChange('city', e.target.value)} placeholder="Cidade" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Logradouro" required><input value={form.address_line} onChange={(e) => handleInputChange('address_line', e.target.value)} placeholder="Logradouro" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Número" required><input value={form.address_number} onChange={(e) => handleInputChange('address_number', e.target.value)} placeholder="Número" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Bairro" required><input value={form.neighborhood} onChange={(e) => handleInputChange('neighborhood', e.target.value)} placeholder="Bairro" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Estado" required><select value={form.state} onChange={(e) => handleInputChange('state', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3"><option value="">Escolha uma opção</option>{stateOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Complemento"><input value={form.address_complement} onChange={(e) => handleInputChange('address_complement', e.target.value)} placeholder="Complemento" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                </div>
                {lookingUpCep && <p className="mt-3 text-sm text-slate-500">Consultando CEP...</p>}
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Informações do veículo</h4></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <VehicleField label="Número do contrato"><input value={form.contract_number} onChange={(e) => handleInputChange('contract_number', e.target.value)} placeholder="Número do contrato" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Situação" required><select value={form.status} onChange={(e) => handleInputChange('status', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3">{statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Data do contrato"><input type="date" value={form.contract_date} onChange={(e) => handleInputChange('contract_date', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Data final do contrato"><input type="date" value={form.contract_end_date} onChange={(e) => handleInputChange('contract_end_date', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Modelo" required><input value={form.model} onChange={(e) => handleInputChange('model', e.target.value)} placeholder="Modelo" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Montadora" required><input value={form.brand} onChange={(e) => handleInputChange('brand', e.target.value)} placeholder="Montadora" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Tipo" required><select value={form.type} onChange={(e) => handleInputChange('type', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3"><option value="">Escolha uma opção</option>{typeOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Placa" required><input value={form.plate} onChange={(e) => handleInputChange('plate', e.target.value)} placeholder="PLACA" className="w-full rounded-xl border border-slate-300 px-4 py-3 uppercase" /></VehicleField>
                  <VehicleField label="Ano fabricação"><input value={form.manufacture_year} onChange={(e) => handleInputChange('manufacture_year', onlyDigits(e.target.value).slice(0, 4))} placeholder="2026" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Ano modelo"><input value={form.model_year} onChange={(e) => handleInputChange('model_year', onlyDigits(e.target.value).slice(0, 4))} placeholder="2026" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Código FIPE"><input value={form.fipe_code} onChange={(e) => handleInputChange('fipe_code', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Valor FIPE"><input value={form.fipe_value} onChange={(e) => handleInputChange('fipe_value', e.target.value)} placeholder="0,00" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Combustível" required><select value={form.fuel_type} onChange={(e) => handleInputChange('fuel_type', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3"><option value="">Escolha uma opção</option>{fuelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Cor" required><select value={form.color} onChange={(e) => handleInputChange('color', e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3"><option value="">Escolha uma opção</option>{colorOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Renavam"><input value={form.renavam} onChange={(e) => handleInputChange('renavam', e.target.value)} placeholder="Renavam" className="w-full rounded-xl border border-slate-300 px-4 py-3" /></VehicleField>
                  <VehicleField label="Chassi"><input value={form.chassis} onChange={(e) => handleInputChange('chassis', e.target.value)} placeholder="CHASSI" className="w-full rounded-xl border border-slate-300 px-4 py-3 uppercase" /></VehicleField>
                </div>
              </Card>

              <Card>
                <div className="mb-4"><h4 className="text-base font-semibold text-slate-900">Imagens / Documentos</h4></div>
                <div className="grid gap-4 md:grid-cols-[220px_1fr] md:items-end">
                  <VehicleField label="Categoria do arquivo"><select value={uploadCategory} onChange={(e) => setUploadCategory(e.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3">{documentCategoryOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></VehicleField>
                  <VehicleField label="Selecione o arquivo"><input multiple type="file" className="w-full rounded-xl border border-slate-300 px-4 py-3" onChange={(e) => setUploadFiles(Array.from(e.target.files || []))} /></VehicleField>
                </div>
                {uploadFiles.length > 0 && <p className="mt-3 text-sm text-slate-500">{uploadFiles.length} arquivo(s) selecionado(s).</p>}
              </Card>

              <div className="flex justify-end gap-3">
                <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={resetForm}>Limpar</Button>
                <Button type="submit" disabled={saving || uploading}>{saving ? 'Salvando...' : isEditing ? 'Atualizar veículo' : 'Cadastrar veículo'}</Button>
              </div>
            </form>
          </Card>

          <Card>
            <h3 className="mb-4 text-lg font-semibold text-slate-900">Arquivos do veículo selecionado</h3>
            {!selectedVehicle ? <p className="text-sm text-slate-500">Selecione um veículo para visualizar os documentos já enviados.</p> : documents.length === 0 ? <p className="text-sm text-slate-500">Nenhum documento vinculado a este veículo.</p> : (
              <div className="space-y-3">
                {documents.map((doc) => (
                  <div key={doc.id} className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-slate-200 p-4">
                    <div>
                      <p className="font-semibold text-slate-900">{doc.file_name}</p>
                      <p className="text-sm text-slate-500">{doc.category} • {doc.review_status}</p>
                      {doc.review_notes && <p className="text-sm text-amber-700">Observação: {doc.review_notes}</p>}
                    </div>
                    <div className="flex gap-2">
                      <div className="flex gap-2"><a href={doc.url} target="_blank" rel="noreferrer" className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Visualizar</a><a href={doc.download_url} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Baixar</a></div>
                      <Button type="button" className="bg-red-600 hover:bg-red-700" onClick={() => handleDeleteDocument(doc.id)}>Excluir</Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </PageShell>
  );
}
