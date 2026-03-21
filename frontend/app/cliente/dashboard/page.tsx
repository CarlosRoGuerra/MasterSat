'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { ClientShell } from '@/components/client-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { clearSession, getAccessToken } from '@/lib/auth';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits } from '@/lib/format';

type ClientDocument = {
  id: number;
  file_name: string;
  category: string;
  content_type: string;
  size_bytes: number;
  review_status: string;
  review_notes?: string | null;
  url: string;
};

type ClientVehicle = {
  id: number;
  plate: string;
  model?: string | null;
  brand?: string | null;
  year?: number | null;
  status: string;
  type?: string | null;
  chassis?: string | null;
  renavam?: string | null;
  color?: string | null;
};

type ClientDashboardData = {
  profile: {
    id: number;
    name: string;
    cpf_cnpj: string;
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
    status: string;
    type: 'pf' | 'pj';
  };
  summary: {
    total_vehicles: number;
    active_vehicles: number;
    pending_billings: number;
    overdue_billings: number;
    total_open_amount: number;
  };
  vehicles: ClientVehicle[];
  recent_billings: Array<{
    id: number;
    amount: number;
    due_date: string;
    status: string;
    payment_date?: string | null;
    payment_method?: string | null;
  }>;
  client_documents: ClientDocument[];
};

type ProfileForm = {
  email: string;
  extra_emails: string;
  phone: string;
  zip_code: string;
  address_line: string;
  address_number: string;
  address_complement: string;
  neighborhood: string;
  city: string;
  state: string;
};

type VehicleForm = {
  plate: string;
  chassis: string;
  renavam: string;
  brand: string;
  model: string;
  year: string;
  color: string;
  type: string;
};

const initialProfileForm: ProfileForm = {
  email: '',
  extra_emails: '',
  phone: '',
  zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
};

const initialVehicleForm: VehicleForm = {
  plate: '',
  chassis: '',
  renavam: '',
  brand: '',
  model: '',
  year: '',
  color: '',
  type: 'carro',
};

const editableStatuses = new Set(['pendente_validacao', 'correcao_solicitada', 'reprovado']);
const clientDocumentOptions = ['cnh', 'rg', 'cpf', 'contrato', 'comprovante_endereco', 'cartao_cnpj', 'contrato_social', 'outro'];
const vehicleDocumentOptions = ['crlv', 'documento_veiculo', 'foto_frontal', 'foto_traseira', 'foto_lateral', 'comprovante_propriedade', 'outro'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function parseExtraEmails(value: string) {
  return value
    .split(/[,;\n]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function formatPlate(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7);
}

function formatChassis(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17);
}

function formatRenavam(value: string) {
  return value.replace(/\D/g, '').slice(0, 11);
}

export default function ClientDashboardPage() {
  const [token, setToken] = useState('');
  const [data, setData] = useState<ClientDashboardData | null>(null);
  const [profileForm, setProfileForm] = useState<ProfileForm>(initialProfileForm);
  const [vehicleForm, setVehicleForm] = useState<VehicleForm>(initialVehicleForm);
  const [editingVehicleId, setEditingVehicleId] = useState<number | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null);
  const [vehicleDocuments, setVehicleDocuments] = useState<ClientDocument[]>([]);
  const [clientDocuments, setClientDocuments] = useState<ClientDocument[]>([]);
  const [profileSaving, setProfileSaving] = useState(false);
  const [vehicleSaving, setVehicleSaving] = useState(false);
  const [clientUploadCategory, setClientUploadCategory] = useState('cnh');
  const [vehicleUploadCategory, setVehicleUploadCategory] = useState('crlv');
  const [clientUploadFile, setClientUploadFile] = useState<File | null>(null);
  const [vehicleUploadFile, setVehicleUploadFile] = useState<File | null>(null);
  const [uploadingClientDoc, setUploadingClientDoc] = useState(false);
  const [uploadingVehicleDoc, setUploadingVehicleDoc] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    const currentToken = getAccessToken();
    if (!currentToken) {
      window.location.href = '/login/cliente';
      return;
    }
    setToken(currentToken);
  }, []);

  async function loadDashboard(currentToken: string) {
    try {
      const response = await apiFetch<ClientDashboardData>('/client-portal/dashboard', {}, currentToken);
      setData(response);
      setClientDocuments(response.client_documents || []);
      setProfileForm((current) => ({
        ...current,
        email: response.profile.email || '',
        extra_emails: (response.profile.extra_emails || []).join('\n'),
        phone: response.profile.phone ? formatPhone(response.profile.phone) : '',
        zip_code: response.profile.zip_code ? formatZipCode(response.profile.zip_code) : '',
        address_line: response.profile.address_line || '',
        address_number: response.profile.address_number || '',
        address_complement: response.profile.address_complement || '',
        neighborhood: response.profile.neighborhood || '',
        city: response.profile.city || '',
        state: response.profile.state || '',
      }));
      if (!selectedVehicleId && response.vehicles.length) {
        setSelectedVehicleId(response.vehicles[0].id);
      }
    } catch (err) {
      const message = parseError(err);
      setError(message);
      if (message.includes('credenciais')) {
        clearSession();
        window.location.href = '/login/cliente';
      }
    }
  }

  useEffect(() => {
    if (!token) return;
    loadDashboard(token);
  }, [token]);

  useEffect(() => {
    if (!token || !selectedVehicleId) {
      setVehicleDocuments([]);
      return;
    }
    apiFetch<ClientDocument[]>(`/client-portal/vehicles/${selectedVehicleId}/documents`, {}, token)
      .then(setVehicleDocuments)
      .catch((err) => setError(parseError(err)));
  }, [token, selectedVehicleId]);

  const selectedVehicle = useMemo(
    () => data?.vehicles.find((vehicle) => vehicle.id === selectedVehicleId) || null,
    [data, selectedVehicleId],
  );

  function resetVehicleForm() {
    setEditingVehicleId(null);
    setVehicleForm(initialVehicleForm);
  }

  function handleVehicleEdit(vehicle: ClientVehicle) {
    setEditingVehicleId(vehicle.id);
    setVehicleForm({
      plate: vehicle.plate || '',
      chassis: vehicle.chassis || '',
      renavam: vehicle.renavam || '',
      brand: vehicle.brand || '',
      model: vehicle.model || '',
      year: vehicle.year ? String(vehicle.year) : '',
      color: vehicle.color || '',
      type: vehicle.type || 'carro',
    });
    setSelectedVehicleId(vehicle.id);
  }

  async function handleProfileSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !data) return;
    setProfileSaving(true);
    setError('');
    setFeedback('');

    try {
      const payload = {
        email: profileForm.email.trim().toLowerCase(),
        extra_emails: data.profile.type === 'pj' ? parseExtraEmails(profileForm.extra_emails) : null,
        phone: profileForm.phone ? onlyDigits(profileForm.phone) : null,
        zip_code: profileForm.zip_code ? onlyDigits(profileForm.zip_code) : null,
        address_line: profileForm.address_line.trim() || null,
        address_number: profileForm.address_number.trim() || null,
        address_complement: profileForm.address_complement.trim() || null,
        neighborhood: profileForm.neighborhood.trim() || null,
        city: profileForm.city.trim() || null,
        state: profileForm.state.trim().toUpperCase() || null,
      };

      await apiFetch<ClientDashboardData['profile']>('/client-portal/profile', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }, token);
      setFeedback('Dados do portal atualizados com sucesso.');
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setProfileSaving(false);
    }
  }

  async function handleVehicleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setVehicleSaving(true);
    setError('');
    setFeedback('');
    try {
      const payload = {
        plate: formatPlate(vehicleForm.plate),
        chassis: vehicleForm.chassis ? formatChassis(vehicleForm.chassis) : null,
        renavam: vehicleForm.renavam ? formatRenavam(vehicleForm.renavam) : null,
        brand: vehicleForm.brand.trim() || null,
        model: vehicleForm.model.trim() || null,
        year: vehicleForm.year ? Number(vehicleForm.year) : null,
        color: vehicleForm.color.trim() || null,
        type: vehicleForm.type.trim() || null,
      };

      if (!payload.plate || payload.plate.length !== 7) {
        throw new Error('Informe uma placa válida.');
      }

      if (editingVehicleId) {
        await apiFetch<ClientVehicle>(`/client-portal/vehicles/${editingVehicleId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Veículo atualizado e reenviado para validação.');
      } else {
        await apiFetch<ClientVehicle>('/client-portal/vehicles', {
          method: 'POST',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Veículo enviado para validação com sucesso.');
      }
      resetVehicleForm();
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setVehicleSaving(false);
    }
  }

  async function handleClientDocumentUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !clientUploadFile) {
      setError('Selecione um documento do cliente para enviar.');
      return;
    }
    setUploadingClientDoc(true);
    setError('');
    setFeedback('');
    try {
      const formData = new FormData();
      formData.append('category', clientUploadCategory);
      formData.append('file', clientUploadFile);
      await apiFetch<ClientDocument>('/client-portal/documents', { method: 'POST', body: formData }, token);
      setClientUploadFile(null);
      setFeedback('Documento do cliente enviado para análise.');
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploadingClientDoc(false);
    }
  }

  async function handleVehicleDocumentUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !selectedVehicleId || !vehicleUploadFile) {
      setError('Selecione um veículo e um arquivo para enviar.');
      return;
    }
    setUploadingVehicleDoc(true);
    setError('');
    setFeedback('');
    try {
      const formData = new FormData();
      formData.append('category', vehicleUploadCategory);
      formData.append('file', vehicleUploadFile);
      await apiFetch<ClientDocument>(`/client-portal/vehicles/${selectedVehicleId}/documents`, { method: 'POST', body: formData }, token);
      setVehicleUploadFile(null);
      setFeedback('Documento do veículo enviado para análise.');
      const docs = await apiFetch<ClientDocument[]>(`/client-portal/vehicles/${selectedVehicleId}/documents`, {}, token);
      setVehicleDocuments(docs);
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploadingVehicleDoc(false);
    }
  }

  return (
    <ClientShell title="Dashboard do cliente">
      {(error || feedback) && (
        <div className="mb-6 space-y-3">
          {error && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card><p className="text-sm text-slate-500">Veículos</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.total_vehicles ?? '--'}</p></Card>
        <Card><p className="text-sm text-slate-500">Ativos / aprovados</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.active_vehicles ?? '--'}</p></Card>
        <Card><p className="text-sm text-slate-500">Cobranças pendentes</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.pending_billings ?? '--'}</p></Card>
        <Card><p className="text-sm text-slate-500">Total em aberto</p><p className="mt-2 text-3xl font-bold text-slate-900">R$ {data?.summary.total_open_amount?.toFixed(2) ?? '--'}</p></Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Perfil do cliente</h3>
          <div className="mb-4 space-y-2 text-sm text-slate-600">
            <p><span className="font-medium text-slate-900">Nome:</span> {data?.profile.name ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Documento:</span> {data?.profile.cpf_cnpj ? formatCpfCnpj(data.profile.cpf_cnpj) : '--'}</p>
            <p><span className="font-medium text-slate-900">Tipo:</span> {data?.profile.type === 'pj' ? 'Pessoa jurídica' : 'Pessoa física'}</p>
            <p><span className="font-medium text-slate-900">Status:</span> {data?.profile.status ?? '--'}</p>
          </div>
          <form className="space-y-4" onSubmit={handleProfileSubmit}>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">E-mail principal</label>
              <input value={profileForm.email} onChange={(e) => setProfileForm((prev) => ({ ...prev, email: e.target.value.toLowerCase() }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
            </div>
            {data?.profile.type === 'pj' && (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">E-mails adicionais</label>
                <textarea value={profileForm.extra_emails} onChange={(e) => setProfileForm((prev) => ({ ...prev, extra_emails: e.target.value }))} className="min-h-[96px] w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" placeholder="Separe por vírgula ou uma linha por e-mail" />
              </div>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Telefone</label>
                <input value={profileForm.phone} onChange={(e) => setProfileForm((prev) => ({ ...prev, phone: formatPhone(e.target.value) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">CEP</label>
                <input value={profileForm.zip_code} onChange={(e) => setProfileForm((prev) => ({ ...prev, zip_code: formatZipCode(e.target.value) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Logradouro</label>
                <input value={profileForm.address_line} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_line: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Número</label>
                <input value={profileForm.address_number} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_number: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Complemento</label>
                <input value={profileForm.address_complement} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_complement: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Bairro</label>
                <input value={profileForm.neighborhood} onChange={(e) => setProfileForm((prev) => ({ ...prev, neighborhood: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cidade</label>
                <input value={profileForm.city} onChange={(e) => setProfileForm((prev) => ({ ...prev, city: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">UF</label>
                <input value={profileForm.state} onChange={(e) => setProfileForm((prev) => ({ ...prev, state: e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <Button type="submit" disabled={profileSaving}>{profileSaving ? 'Salvando...' : 'Atualizar dados do portal'}</Button>
          </form>
        </Card>

        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Cadastrar ou editar veículo</h3>
          <form className="space-y-4" onSubmit={handleVehicleSubmit}>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Placa</label>
                <input value={vehicleForm.plate} onChange={(e) => setVehicleForm((prev) => ({ ...prev, plate: formatPlate(e.target.value) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={vehicleForm.type} onChange={(e) => setVehicleForm((prev) => ({ ...prev, type: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="carro">Carro</option>
                  <option value="moto">Moto</option>
                  <option value="caminhao">Caminhão</option>
                  <option value="utilitario">Utilitário</option>
                  <option value="outros">Outros</option>
                </select>
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Marca</label>
                <input value={vehicleForm.brand} onChange={(e) => setVehicleForm((prev) => ({ ...prev, brand: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Modelo</label>
                <input value={vehicleForm.model} onChange={(e) => setVehicleForm((prev) => ({ ...prev, model: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Chassi</label>
                <input value={vehicleForm.chassis} onChange={(e) => setVehicleForm((prev) => ({ ...prev, chassis: formatChassis(e.target.value) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">RENAVAM</label>
                <input value={vehicleForm.renavam} onChange={(e) => setVehicleForm((prev) => ({ ...prev, renavam: formatRenavam(e.target.value) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Ano</label>
                <input value={vehicleForm.year} onChange={(e) => setVehicleForm((prev) => ({ ...prev, year: e.target.value.replace(/\D/g, '').slice(0, 4) }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cor</label>
                <input value={vehicleForm.color} onChange={(e) => setVehicleForm((prev) => ({ ...prev, color: e.target.value }))} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button type="submit" disabled={vehicleSaving}>{vehicleSaving ? 'Enviando...' : editingVehicleId ? 'Salvar e reenviar para validação' : 'Cadastrar veículo'}</Button>
              {editingVehicleId ? <button type="button" className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" onClick={resetVehicleForm}>Cancelar edição</button> : null}
            </div>
          </form>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">Meus veículos</h3>
              <p className="text-sm text-slate-500">Cadastre, acompanhe a validação e reenvie correções quando necessário.</p>
            </div>
          </div>
          <div className="space-y-3 text-sm text-slate-600">
            {data?.vehicles.length ? data.vehicles.map((vehicle) => (
              <div key={vehicle.id} className={`rounded-2xl border p-4 ${selectedVehicleId === vehicle.id ? 'border-brand-300 bg-brand-50/30' : 'border-slate-200'}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-slate-900">{vehicle.plate}</p>
                    <p>{vehicle.brand || 'Marca não informada'} • {vehicle.model || 'Modelo não informado'}</p>
                    <p>Ano: {vehicle.year || '--'} • Status: {vehicle.status}</p>
                    <p>Tipo: {vehicle.type || '--'} • RENAVAM: {vehicle.renavam || '--'}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700" onClick={() => setSelectedVehicleId(vehicle.id)}>Documentos</button>
                    {editableStatuses.has(vehicle.status) ? (
                      <button type="button" className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700" onClick={() => handleVehicleEdit(vehicle)}>Editar</button>
                    ) : null}
                  </div>
                </div>
              </div>
            )) : <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Você ainda não cadastrou veículos no portal.</p>}
          </div>
        </Card>

        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Documentos do veículo</h3>
          {selectedVehicle ? (
            <>
              <p className="mb-4 text-sm text-slate-500">Veículo selecionado: <span className="font-semibold text-slate-900">{selectedVehicle.plate}</span></p>
              <form className="mb-5 space-y-3" onSubmit={handleVehicleDocumentUpload}>
                <div>
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Categoria</label>
                  <select value={vehicleUploadCategory} onChange={(e) => setVehicleUploadCategory(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                    {vehicleDocumentOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </div>
                <input type="file" onChange={(e) => setVehicleUploadFile(e.target.files?.[0] || null)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
                <Button type="submit" disabled={uploadingVehicleDoc}>{uploadingVehicleDoc ? 'Enviando...' : 'Enviar documento do veículo'}</Button>
              </form>
              <div className="space-y-3 text-sm text-slate-600">
                {vehicleDocuments.length ? vehicleDocuments.map((document) => (
                  <div key={document.id} className="rounded-2xl border border-slate-200 p-4">
                    <p className="font-semibold text-slate-900">{document.file_name}</p>
                    <p>Categoria: {document.category}</p>
                    <p>Status: {document.review_status}</p>
                    {document.review_notes ? <p>Retorno do administrativo: {document.review_notes}</p> : null}
                    <a href={document.url} target="_blank" className="mt-2 inline-flex text-sm font-semibold text-brand-600">Abrir arquivo</a>
                  </div>
                )) : <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Nenhum documento enviado para este veículo.</p>}
              </div>
            </>
          ) : <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Selecione um veículo para enviar e acompanhar documentos.</p>}
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Documentos do cliente</h3>
          <form className="mb-5 space-y-3" onSubmit={handleClientDocumentUpload}>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Categoria</label>
              <select value={clientUploadCategory} onChange={(e) => setClientUploadCategory(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                {clientDocumentOptions.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </div>
            <input type="file" onChange={(e) => setClientUploadFile(e.target.files?.[0] || null)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit" disabled={uploadingClientDoc}>{uploadingClientDoc ? 'Enviando...' : 'Enviar documento do cliente'}</Button>
          </form>
          <div className="space-y-3 text-sm text-slate-600">
            {clientDocuments.length ? clientDocuments.map((document) => (
              <div key={document.id} className="rounded-2xl border border-slate-200 p-4">
                <p className="font-semibold text-slate-900">{document.file_name}</p>
                <p>Categoria: {document.category}</p>
                <p>Status: {document.review_status}</p>
                {document.review_notes ? <p>Retorno do administrativo: {document.review_notes}</p> : null}
                <a href={document.url} target="_blank" className="mt-2 inline-flex text-sm font-semibold text-brand-600">Abrir arquivo</a>
              </div>
            )) : <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Nenhum documento pessoal/empresarial enviado.</p>}
          </div>
        </Card>

        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Cobranças recentes</h3>
          <div className="space-y-3 text-sm text-slate-600">
            {data?.recent_billings.length ? data.recent_billings.map((billing) => (
              <div key={billing.id} className="rounded-2xl border border-slate-200 p-4">
                <p className="font-semibold text-slate-900">R$ {billing.amount.toFixed(2)}</p>
                <p>Vencimento: {new Date(`${billing.due_date}T00:00:00`).toLocaleDateString('pt-BR')}</p>
                <p>Status: {billing.status}</p>
                <p>Pagamento: {billing.payment_date ? new Date(`${billing.payment_date}T00:00:00`).toLocaleDateString('pt-BR') : '--'}</p>
              </div>
            )) : <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Nenhuma cobrança disponível.</p>}
          </div>
        </Card>
      </div>
    </ClientShell>
  );
}
