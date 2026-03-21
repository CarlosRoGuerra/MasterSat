'use client';

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { clearSession, getAccessToken } from '@/lib/auth';

type ClientOption = {
  id: number;
  name: string;
  cpf_cnpj: string;
};

type VehicleStatus = 'pendente_validacao' | 'em_analise' | 'aprovado' | 'reprovado' | 'correcao_solicitada' | 'ativo' | 'sem_rastreador' | 'retirado' | 'bloqueado';

type Vehicle = {
  id: number;
  plate: string;
  chassis?: string | null;
  renavam?: string | null;
  brand?: string | null;
  model?: string | null;
  year?: number | null;
  color?: string | null;
  type?: string | null;
  status: VehicleStatus;
  client_id: number;
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
};

type VehicleFormState = {
  plate: string;
  chassis: string;
  renavam: string;
  brand: string;
  model: string;
  year: string;
  color: string;
  type: string;
  status: VehicleStatus;
  client_id: string;
};

const initialForm: VehicleFormState = {
  plate: '',
  chassis: '',
  renavam: '',
  brand: '',
  model: '',
  year: '',
  color: '',
  type: 'carro',
  status: 'ativo',
  client_id: '',
};

const statusOptions: VehicleStatus[] = ['pendente_validacao', 'em_analise', 'aprovado', 'reprovado', 'correcao_solicitada', 'ativo', 'sem_rastreador', 'retirado', 'bloqueado'];
const typeOptions = ['carro', 'moto', 'caminhao', 'outros'];
const categoryOptions = ['foto_frontal', 'foto_lateral', 'foto_traseira', 'crlv', 'documento_veiculo', 'comprovante_propriedade', 'outro'];

function formatPlate(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7);
}

function formatRenavam(value: string) {
  return value.replace(/\D/g, '').slice(0, 11);
}

function formatChassis(value: string) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17);
}

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export default function VehiclesPage() {
  const [token, setToken] = useState('');
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
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadCategory, setUploadCategory] = useState('foto_frontal');
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    const currentToken = getAccessToken();
    if (!currentToken) {
      window.location.href = '/login/admin';
      return;
    }
    setToken(currentToken);
  }, []);

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
      if (message.includes('credenciais')) {
        clearSession();
        window.location.href = '/login/admin';
      }
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
    setUploadFile(null);
    setUploadCategory('foto_frontal');
    setIsEditing(false);
  }

  function handleEdit(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setForm({
      plate: vehicle.plate,
      chassis: vehicle.chassis ?? '',
      renavam: vehicle.renavam ?? '',
      brand: vehicle.brand ?? '',
      model: vehicle.model ?? '',
      year: vehicle.year ? String(vehicle.year) : '',
      color: vehicle.color ?? '',
      type: vehicle.type ?? 'carro',
      status: vehicle.status,
      client_id: String(vehicle.client_id),
    });
    setIsEditing(true);
    if (token) loadDocuments(vehicle.id, token);
  }

  function handleInputChange(field: keyof VehicleFormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setSaving(true);
    setFeedback('');
    setError('');
    try {
      const payload = {
        plate: formatPlate(form.plate),
        chassis: form.chassis ? formatChassis(form.chassis) : null,
        renavam: form.renavam ? formatRenavam(form.renavam) : null,
        brand: form.brand || null,
        model: form.model || null,
        year: form.year ? Number(form.year) : null,
        color: form.color || null,
        type: form.type || null,
        status: form.status,
        client_id: Number(form.client_id),
      };

      if (!payload.client_id) {
        throw new Error('Selecione o cliente vinculado ao veículo.');
      }

      if (isEditing && selectedVehicle) {
        await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Veículo atualizado com sucesso.');
      } else {
        await apiFetch<Vehicle>('/vehicles', {
          method: 'POST',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Veículo cadastrado com sucesso.');
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
      if (selectedVehicle?.id === vehicleId) {
        resetForm();
      }
      await loadBaseData(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !selectedVehicle || !uploadFile) {
      setError('Selecione um veículo e um arquivo para enviar.');
      return;
    }
    setUploading(true);
    setError('');
    setFeedback('');
    try {
      const formData = new FormData();
      formData.append('category', uploadCategory);
      formData.append('file', uploadFile);
      await apiFetch<VehicleDocument>(`/vehicles/${selectedVehicle.id}/documents`, {
        method: 'POST',
        body: formData,
      }, token);
      setFeedback('Documento enviado para o MinIO com sucesso.');
      setUploadFile(null);
      await loadDocuments(selectedVehicle.id, token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
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

  const selectedClientName = useMemo(() => {
    if (!selectedVehicle) return '';
    return clients.find((client) => client.id === selectedVehicle.client_id)?.name || '';
  }, [clients, selectedVehicle]);

  return (
    <PageShell title="Veículos">
      {(error || feedback) && (
        <div className="mb-6 space-y-3">
          {error && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[1.3fr_0.7fr]">
        <div className="space-y-6">
          <Card>
            <div className="mb-4 flex flex-wrap items-end gap-3">
              <div className="min-w-[220px] flex-1">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Busca</label>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Placa, chassi, RENAVAM, marca ou modelo" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div className="min-w-[160px]">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Status</label>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div className="min-w-[180px]">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cliente</label>
                <select value={clientFilter} onChange={(e) => setClientFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
                </select>
              </div>
              <div className="min-w-[150px]">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {typeOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <Button type="button" onClick={() => loadBaseData(token)} disabled={loading}>Filtrar</Button>
                <button type="button" onClick={() => { setSearch(''); setStatusFilter(''); setClientFilter(''); setTypeFilter(''); setTimeout(() => loadBaseData(token), 0); }} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Limpar</button>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-[0.14em] text-slate-500">
                    <th className="px-3 py-3">Placa</th>
                    <th className="px-3 py-3">Cliente</th>
                    <th className="px-3 py-3">Veículo</th>
                    <th className="px-3 py-3">Status</th>
                    <th className="px-3 py-3">Ano</th>
                    <th className="px-3 py-3">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loading ? (
                    <tr><td className="px-3 py-6 text-slate-500" colSpan={6}>Carregando veículos...</td></tr>
                  ) : vehicles.length ? (
                    vehicles.map((vehicle) => {
                      const client = clients.find((item) => item.id === vehicle.client_id);
                      return (
                        <tr key={vehicle.id} className="hover:bg-slate-50">
                          <td className="px-3 py-3 font-semibold text-slate-900">{vehicle.plate}</td>
                          <td className="px-3 py-3 text-slate-600">{client?.name || `#${vehicle.client_id}`}</td>
                          <td className="px-3 py-3 text-slate-600">{vehicle.brand || '--'} {vehicle.model || ''}</td>
                          <td className="px-3 py-3 text-slate-600">{vehicle.status}</td>
                          <td className="px-3 py-3 text-slate-600">{vehicle.year || '--'}</td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-2">
                              <button type="button" onClick={() => handleEdit(vehicle)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">Editar</button>
                              <button type="button" onClick={() => handleDelete(vehicle.id)} className="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50">Excluir</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr><td className="px-3 py-6 text-slate-500" colSpan={6}>Nenhum veículo encontrado com os filtros aplicados.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>

          <Card>
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Documentos do veículo</h3>
                <p className="text-sm text-slate-500">Arquivos enviados são armazenados no MinIO e listados com link temporário.</p>
              </div>
              <div className="text-right text-sm text-slate-500">
                <p className="font-semibold text-slate-900">{selectedVehicle ? selectedVehicle.plate : '--'}</p>
                <p>{selectedClientName || 'Selecione um veículo para gerenciar anexos'}</p>
              </div>
            </div>

            {!selectedVehicle ? (
              <p className="rounded-2xl border border-dashed border-slate-300 p-6 text-sm text-slate-500">Escolha um veículo na tabela para visualizar e subir documentos.</p>
            ) : (
              <div className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
                <form onSubmit={handleUpload} className="space-y-4 rounded-2xl border border-slate-200 p-4">
                  <div>
                    <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Categoria</label>
                    <select value={uploadCategory} onChange={(e) => setUploadCategory(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                      {categoryOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Arquivo</label>
                    <input type="file" onChange={(e: ChangeEvent<HTMLInputElement>) => setUploadFile(e.target.files?.[0] || null)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
                  </div>
                  <Button type="submit" disabled={uploading}>{uploading ? 'Enviando...' : 'Enviar para o MinIO'}</Button>
                </form>

                <div className="space-y-3">
                  {documents.length ? documents.map((document) => (
                    <div key={document.id} className="rounded-2xl border border-slate-200 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900">{document.file_name}</p>
                          <p className="text-sm text-slate-500">Categoria: {document.category}</p>
                          {document.review_status ? <p className="text-sm text-slate-500">Validação: {document.review_status}</p> : null}
                          {document.review_notes ? <p className="text-sm text-slate-500">Observação: {document.review_notes}</p> : null}
                          <p className="text-sm text-slate-500">Tamanho: {(document.size_bytes / 1024).toFixed(1)} KB</p>
                        </div>
                        <div className="flex gap-2">
                          <a href={document.url} target="_blank" className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Abrir</a>
                          <button type="button" onClick={() => handleDeleteDocument(document.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50">Remover</button>
                        </div>
                      </div>
                    </div>
                  )) : (
                    <p className="rounded-2xl border border-dashed border-slate-300 p-6 text-sm text-slate-500">Nenhum documento enviado para este veículo.</p>
                  )}
                </div>
              </div>
            )}
          </Card>
        </div>

        <Card>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">{isEditing ? 'Editar veículo' : 'Novo veículo'}</h3>
              <p className="text-sm text-slate-500">Cadastro completo com validação e vínculo ao cliente.</p>
            </div>
            {(isEditing || selectedVehicle) && (
              <button type="button" onClick={resetForm} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Novo cadastro</button>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cliente</label>
              <select required value={form.client_id} onChange={(e) => handleInputChange('client_id', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                <option value="">Selecione</option>
                {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
              </select>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Placa</label>
                <input required value={form.plate} onChange={(e) => handleInputChange('plate', formatPlate(e.target.value))} placeholder="ABC1234" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Chassi</label>
                <input value={form.chassis} onChange={(e) => handleInputChange('chassis', formatChassis(e.target.value))} placeholder="9BWZZZ377VT004251" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">RENAVAM</label>
                <input value={form.renavam} onChange={(e) => handleInputChange('renavam', formatRenavam(e.target.value))} placeholder="12345678901" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Ano</label>
                <input value={form.year} onChange={(e) => handleInputChange('year', e.target.value.replace(/\D/g, '').slice(0, 4))} placeholder="2024" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Marca</label>
                <input value={form.brand} onChange={(e) => handleInputChange('brand', e.target.value)} placeholder="Volkswagen" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Modelo</label>
                <input value={form.model} onChange={(e) => handleInputChange('model', e.target.value)} placeholder="Gol" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cor</label>
                <input value={form.color} onChange={(e) => handleInputChange('color', e.target.value)} placeholder="Branco" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={form.type} onChange={(e) => handleInputChange('type', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  {typeOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Status</label>
              <select value={form.status} onChange={(e) => handleInputChange('status', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                {statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </div>
            <Button type="submit" disabled={saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar veículo' : 'Cadastrar veículo'}</Button>
          </form>
        </Card>
      </div>
    </PageShell>
  );
}
