'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { ClientShell } from '@/components/client-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { fetchAddressByCep } from '@/lib/cep';
import { formatPhone, formatZipCode, onlyDigits } from '@/lib/format';

type ClientDocument = {
  id: number;
  file_name: string;
  category: string;
  content_type: string;
  size_bytes: number;
  review_status: string;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

type ClientVehicle = {
  id: number;
  plate: string;
  model?: string | null;
  brand?: string | null;
  year?: number | null;
  manufacture_year?: number | null;
  model_year?: number | null;
  status: string;
  type?: string | null;
  chassis?: string | null;
  renavam?: string | null;
  color?: string | null;
  contract_number?: string | null;
  fuel_type?: string | null;
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

const clientDocumentOptions = ['cnh', 'rg', 'cpf', 'contrato', 'comprovante_endereco', 'cartao_cnpj', 'contrato_social', 'outro'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function parseExtraEmails(value: string) {
  return value
    .split(/[,;\n]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

export default function ClientDashboardPage() {
  const [data, setData] = useState<ClientDashboardData | null>(null);
  const [profileForm, setProfileForm] = useState<ProfileForm>(initialProfileForm);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null);
  const [vehicleDocuments, setVehicleDocuments] = useState<ClientDocument[]>([]);
  const [clientDocuments, setClientDocuments] = useState<ClientDocument[]>([]);
  const [profileSaving, setProfileSaving] = useState(false);
  const [clientUploadCategory, setClientUploadCategory] = useState('cnh');
  const [clientUploadFile, setClientUploadFile] = useState<File | null>(null);
  const [uploadingClientDoc, setUploadingClientDoc] = useState(false);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['cliente'], '/login/cliente');

  async function loadDashboard(currentToken: string) {
    try {
      const response = await apiFetch<ClientDashboardData>('/client-portal/dashboard', {}, currentToken);
      setData(response);
      setClientDocuments(response.client_documents || []);
      setProfileForm({
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
      });
      if (!selectedVehicleId && response.vehicles.length) {
        setSelectedVehicleId(response.vehicles[0].id);
      }
    } catch (err) {
      const message = parseError(err);
      setError(message);
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

  async function fillAddressFromCep(rawCep: string) {
    const cep = onlyDigits(rawCep);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    setError('');
    try {
      const result = await fetchAddressByCep(cep);
      if (!result) return;
      setProfileForm((prev) => ({
        ...prev,
        zip_code: formatZipCode(result.zip_code),
        address_line: prev.address_line || result.address_line,
        address_complement: prev.address_complement || result.address_complement,
        neighborhood: prev.neighborhood || result.neighborhood,
        city: prev.city || result.city,
        state: prev.state || result.state,
      }));
      setFeedback('Endereço preenchido automaticamente pelo CEP.');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLookingUpCep(false);
    }
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

      await apiFetch('/client-portal/profile', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }, token);
      setFeedback('Perfil atualizado com sucesso.');
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setProfileSaving(false);
    }
  }

  async function handleClientUpload(event: FormEvent<HTMLFormElement>) {
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
      await apiFetch<ClientDocument>('/client-portal/documents', {
        method: 'POST',
        body: formData,
      }, token);
      setClientUploadFile(null);
      setFeedback('Documento enviado com sucesso.');
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploadingClientDoc(false);
    }
  }

  async function handleDeleteClientDocument(documentId: number) {
    if (!token) return;
    if (!window.confirm('Remover este documento?')) return;
    setError('');
    setFeedback('');
    try {
      await apiFetch(`/client-portal/documents/${documentId}`, { method: 'DELETE' }, token);
      setFeedback('Documento removido com sucesso.');
      await loadDashboard(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <ClientShell title="Minha dashboard">
      {(guardError || error || feedback) && (
        <div className="mb-6 space-y-3">
          {(guardError || error) && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{guardError || error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      {guardLoading && <p className="mb-4 text-sm text-slate-500">Validando sessão...</p>}
      <div className="mb-6 grid gap-4 md:grid-cols-4">
        <Card><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Veículos</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.total_vehicles ?? 0}</p></Card>
        <Card><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Veículos ativos</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.active_vehicles ?? 0}</p></Card>
        <Card><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Cobranças pendentes</p><p className="mt-2 text-3xl font-bold text-slate-900">{data?.summary.pending_billings ?? 0}</p></Card>
        <Card><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Em aberto</p><p className="mt-2 text-3xl font-bold text-slate-900">R$ {(data?.summary.total_open_amount ?? 0).toFixed(2)}</p></Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="space-y-6">
          <Card>
            <h3 className="mb-4 text-lg font-semibold text-slate-900">Meu perfil</h3>
            <div className="mb-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-600">
              <p><strong>Nome:</strong> {data?.profile.name || '-'}</p>
              <p><strong>Documento:</strong> {data?.profile.cpf_cnpj || '-'}</p>
              <p><strong>Status:</strong> {data?.profile.status || '-'}</p>
            </div>
            <form onSubmit={handleProfileSubmit} className="grid gap-4 md:grid-cols-2">
              <label className="block md:col-span-2"><span className="mb-2 block text-sm font-medium text-slate-700">E-mail principal</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.email} onChange={(e) => setProfileForm((prev) => ({ ...prev, email: e.target.value.toLowerCase() }))} /></label>
              {data?.profile.type === 'pj' && (
                <label className="block md:col-span-2"><span className="mb-2 block text-sm font-medium text-slate-700">E-mails adicionais</span><textarea className="min-h-[90px] w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.extra_emails} onChange={(e) => setProfileForm((prev) => ({ ...prev, extra_emails: e.target.value }))} /></label>
              )}
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Telefone</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.phone} onChange={(e) => setProfileForm((prev) => ({ ...prev, phone: formatPhone(e.target.value) }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">CEP</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.zip_code} onChange={(e) => setProfileForm((prev) => ({ ...prev, zip_code: formatZipCode(e.target.value) }))} onBlur={(e) => fillAddressFromCep(e.target.value)} /></label>
              <label className="block md:col-span-2"><span className="mb-2 block text-sm font-medium text-slate-700">Logradouro</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.address_line} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_line: e.target.value }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Número</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.address_number} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_number: e.target.value }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Complemento</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.address_complement} onChange={(e) => setProfileForm((prev) => ({ ...prev, address_complement: e.target.value }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Bairro</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.neighborhood} onChange={(e) => setProfileForm((prev) => ({ ...prev, neighborhood: e.target.value }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Cidade</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.city} onChange={(e) => setProfileForm((prev) => ({ ...prev, city: e.target.value }))} /></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">UF</span><input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={profileForm.state} onChange={(e) => setProfileForm((prev) => ({ ...prev, state: e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))} /></label>
              <div className="md:col-span-2 flex justify-end"><Button type="submit" disabled={profileSaving || lookingUpCep}>{profileSaving ? 'Salvando...' : 'Salvar perfil'}</Button></div>
            </form>
          </Card>

          <Card>
            <h3 className="mb-4 text-lg font-semibold text-slate-900">Documentos do cliente</h3>
            <form onSubmit={handleClientUpload} className="mb-5 grid gap-4 md:grid-cols-[180px_1fr_auto] md:items-end">
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Categoria</span><select className="w-full rounded-xl border border-slate-300 px-4 py-3" value={clientUploadCategory} onChange={(e) => setClientUploadCategory(e.target.value)}>{clientDocumentOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
              <label className="block"><span className="mb-2 block text-sm font-medium text-slate-700">Arquivo</span><input type="file" className="w-full rounded-xl border border-slate-300 px-4 py-3" onChange={(e) => setClientUploadFile(e.target.files?.[0] || null)} /></label>
              <div className="flex justify-end"><Button type="submit" disabled={uploadingClientDoc}>{uploadingClientDoc ? 'Enviando...' : 'Enviar documento'}</Button></div>
            </form>
            <div className="space-y-3">
              {clientDocuments.length === 0 ? <p className="text-sm text-slate-500">Nenhum documento enviado.</p> : clientDocuments.map((doc) => (
                <div key={doc.id} className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-slate-200 p-4">
                  <div>
                    <p className="font-semibold text-slate-900">{doc.file_name}</p>
                    <p className="text-sm text-slate-500">{doc.category} • {doc.review_status}</p>
                    {doc.review_notes && <p className="text-sm text-amber-700">Observação: {doc.review_notes}</p>}
                  </div>
                  <div className="flex gap-2">
                    <div className="flex gap-2"><a href={doc.url} target="_blank" rel="noreferrer" className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Visualizar</a><a href={doc.download_url} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Baixar</a></div>
                    <Button type="button" className="bg-red-600 hover:bg-red-700" onClick={() => handleDeleteClientDocument(doc.id)}>Excluir</Button>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Meus veículos</h3>
                <p className="text-sm text-slate-500">O cadastro e a manutenção dos veículos são realizados pela equipe administrativa.</p>
              </div>
            </div>

            {data?.vehicles.length ? (
              <div className="space-y-3">
                {data.vehicles.map((vehicle) => (
                  <button key={vehicle.id} type="button" onClick={() => setSelectedVehicleId(vehicle.id)} className={`w-full rounded-2xl border p-4 text-left ${selectedVehicleId === vehicle.id ? 'border-brand-500 bg-brand-50' : 'border-slate-200 bg-white'}`}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-slate-900">{vehicle.plate}</p>
                        <p className="text-sm text-slate-500">{[vehicle.brand, vehicle.model].filter(Boolean).join(' • ') || 'Veículo cadastrado'}</p>
                        <p className="text-sm text-slate-500">Status: {vehicle.status}</p>
                      </div>
                      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{vehicle.type || 'sem tipo'}</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Nenhum veículo vinculado até o momento.</p>
            )}
          </Card>

          <Card>
            <h3 className="mb-4 text-lg font-semibold text-slate-900">Detalhes do veículo</h3>
            {selectedVehicle ? (
              <div className="space-y-5">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Placa</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.plate}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Contrato</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.contract_number || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Modelo</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.model || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Montadora</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.brand || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Ano fabricação / modelo</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.manufacture_year || '-'} / {selectedVehicle.model_year || selectedVehicle.year || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Combustível / cor</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.fuel_type || '-'} / {selectedVehicle.color || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">Chassi</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.chassis || '-'}</p></div>
                  <div className="rounded-2xl bg-slate-50 p-4"><p className="text-xs uppercase tracking-[0.12em] text-slate-500">RENAVAM</p><p className="mt-1 font-semibold text-slate-900">{selectedVehicle.renavam || '-'}</p></div>
                </div>

                <div>
                  <h4 className="mb-3 text-sm font-semibold uppercase tracking-[0.12em] text-slate-500">Documentos do veículo</h4>
                  <div className="space-y-3">
                    {vehicleDocuments.length === 0 ? <p className="text-sm text-slate-500">Nenhum documento vinculado a este veículo.</p> : vehicleDocuments.map((doc) => (
                      <div key={doc.id} className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-slate-200 p-4">
                        <div>
                          <p className="font-semibold text-slate-900">{doc.file_name}</p>
                          <p className="text-sm text-slate-500">{doc.category} • {doc.review_status}</p>
                          {doc.review_notes && <p className="text-sm text-amber-700">Observação: {doc.review_notes}</p>}
                        </div>
                        <div className="flex gap-2"><a href={doc.url} target="_blank" rel="noreferrer" className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Visualizar</a><a href={doc.download_url} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Baixar</a></div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Selecione um veículo para ver os detalhes.</p>
            )}
          </Card>

          <Card>
            <h3 className="mb-4 text-lg font-semibold text-slate-900">Cobranças recentes</h3>
            <div className="space-y-3">
              {data?.recent_billings.length ? data.recent_billings.map((billing) => (
                <div key={billing.id} className="rounded-2xl border border-slate-200 p-4">
                  <p className="font-semibold text-slate-900">R$ {billing.amount.toFixed(2)}</p>
                  <p className="text-sm text-slate-500">Vencimento: {billing.due_date} • Status: {billing.status}</p>
                  {billing.payment_date && <p className="text-sm text-emerald-700">Pago em {billing.payment_date}</p>}
                </div>
              )) : <p className="text-sm text-slate-500">Nenhuma cobrança encontrada.</p>}
            </div>
          </Card>
        </div>
      </div>
    </ClientShell>
  );
}
