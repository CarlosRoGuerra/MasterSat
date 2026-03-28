'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { fetchAddressByCep } from '@/lib/cep';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits } from '@/lib/format';

type ClientStatus = 'ativo' | 'inativo' | 'inadimplente' | 'suspenso';
type ClientType = 'pf' | 'pj';

type Client = {
  id: number;
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
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
  address?: string | null;
  notes?: string | null;
};

type ClientFormState = {
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
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
  address: string;
  notes: string;
};

const initialForm: ClientFormState = {
  name: '',
  cpf_cnpj: '',
  type: 'pf',
  status: 'ativo',
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
  address: '',
  notes: '',
};

const statusOptions: ClientStatus[] = ['ativo', 'inativo', 'inadimplente', 'suspenso'];
const typeOptions: ClientType[] = ['pf', 'pj'];

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function parseExtraEmails(value: string) {
  return value
    .split(/[,;\n]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export default function ClientsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [form, setForm] = useState<ClientFormState>(initialForm);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  const { token, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');

  async function loadClients(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (typeFilter) query.set('type', typeFilter);
      query.set('limit', '200');
      const response = await apiFetch<Client[]>(`/clients?${query.toString()}`, {}, currentToken);
      setClients(response);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadClients(token);
  }, [token]);

  const filteredClients = useMemo(() => clients, [clients]);

  function resetForm() {
    setForm(initialForm);
    setSelectedClient(null);
    setIsEditing(false);
  }

  function handleEdit(client: Client) {
    setSelectedClient(client);
    setForm({
      name: client.name || '',
      cpf_cnpj: formatCpfCnpj(client.cpf_cnpj || ''),
      type: client.type || 'pf',
      status: client.status || 'ativo',
      email: client.email || '',
      extra_emails: (client.extra_emails || []).join('\n'),
      phone: formatPhone(client.phone || ''),
      zip_code: formatZipCode(client.zip_code || ''),
      address_line: client.address_line || '',
      address_number: client.address_number || '',
      address_complement: client.address_complement || '',
      neighborhood: client.neighborhood || '',
      city: client.city || '',
      state: client.state || '',
      address: client.address || '',
      notes: client.notes || '',
    });
    setIsEditing(true);
  }

  function handleChange(field: keyof ClientFormState, value: string) {
    let nextValue = value;
    if (field === 'cpf_cnpj') nextValue = formatCpfCnpj(value);
    if (field === 'phone') nextValue = formatPhone(value);
    if (field === 'zip_code') nextValue = formatZipCode(value);
    if (field === 'state') nextValue = value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
    setForm((prev) => ({ ...prev, [field]: nextValue }));
  }

  async function fillAddressFromCep(rawCep: string) {
    const cep = onlyDigits(rawCep);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    try {
      const result = await fetchAddressByCep(cep);
      if (!result) return;
      setForm((prev) => ({
        ...prev,
        zip_code: formatZipCode(result.zip_code),
        address_line: prev.address_line || result.address_line,
        neighborhood: prev.neighborhood || result.neighborhood,
        city: prev.city || result.city,
        state: prev.state || result.state,
        address_complement: prev.address_complement || result.address_complement,
      }));
      setFeedback('Endereço preenchido automaticamente pelo CEP.');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLookingUpCep(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setSaving(true);
    setError('');
    setFeedback('');

    try {
      const payload = {
        name: form.name.trim(),
        cpf_cnpj: onlyDigits(form.cpf_cnpj),
        type: form.type,
        status: form.status,
        email: form.email ? normalizeEmail(form.email) : null,
        extra_emails: form.type === 'pj' ? parseExtraEmails(form.extra_emails) : null,
        phone: form.phone ? onlyDigits(form.phone) : null,
        zip_code: form.zip_code ? onlyDigits(form.zip_code) : null,
        address_line: form.address_line.trim() || null,
        address_number: form.address_number.trim() || null,
        address_complement: form.address_complement.trim() || null,
        neighborhood: form.neighborhood.trim() || null,
        city: form.city.trim() || null,
        state: form.state.trim().toUpperCase() || null,
        address: form.address.trim() || null,
        notes: form.notes.trim() || null,
      };

      if (!payload.name) throw new Error('Informe o nome do cliente.');
      if (![11, 14].includes(payload.cpf_cnpj.length)) throw new Error('Informe um CPF ou CNPJ válido.');
      if (payload.phone && ![10, 11].includes(payload.phone.length)) throw new Error('Telefone inválido.');
      if (payload.type === 'pj' && payload.extra_emails?.some((item) => !item.includes('@'))) throw new Error('Revise os e-mails adicionais.');
      if (payload.zip_code && payload.zip_code.length !== 8) throw new Error('CEP inválido.');
      if (payload.state && payload.state.length !== 2) throw new Error('UF inválida.');

      if (isEditing && selectedClient) {
        await apiFetch<Client>(`/clients/${selectedClient.id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Cliente atualizado com sucesso.');
      } else {
        await apiFetch<Client>('/clients', {
          method: 'POST',
          body: JSON.stringify(payload),
        }, token);
        setFeedback('Cliente cadastrado com sucesso.');
      }

      resetForm();
      await loadClients(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(clientId: number) {
    if (!token) return;
    if (!window.confirm('Deseja realmente remover este cliente?')) return;
    setError('');
    setFeedback('');
    try {
      await apiFetch<{ message: string }>(`/clients/${clientId}`, { method: 'DELETE' }, token);
      setFeedback('Cliente removido com sucesso.');
      if (selectedClient?.id === clientId) resetForm();
      await loadClients(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Clientes">
      {(guardError || error || feedback) && (
        <div className="mb-6 space-y-3">
          {(guardError || error) && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{guardError || error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          {guardLoading && <p className="text-sm text-slate-500">Validando sessão...</p>}
          <Card>
            <div className="mb-4 grid gap-3 md:grid-cols-[1fr_180px_160px_auto]">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Busca</label>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nome, CPF/CNPJ, e-mail, telefone ou cidade" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Status</label>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {typeOptions.map((type) => <option key={type} value={type}>{type.toUpperCase()}</option>)}
                </select>
              </div>
              <div className="flex items-end">
                <Button type="button" className="w-full" onClick={() => loadClients(token)}>
                  Atualizar
                </Button>
              </div>
            </div>

            {loading ? (
              <p className="text-sm text-slate-500">Carregando clientes...</p>
            ) : filteredClients.length === 0 ? (
              <p className="text-sm text-slate-500">Nenhum cliente encontrado.</p>
            ) : (
              <div className="space-y-3">
                {filteredClients.map((client) => (
                  <div key={client.id} className="rounded-2xl border border-slate-200 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <h3 className="text-base font-semibold text-slate-900">{client.name}</h3>
                        <p className="text-sm text-slate-500">{client.cpf_cnpj} • {client.type.toUpperCase()} • {client.status}</p>
                        <p className="text-sm text-slate-500">{client.email || 'Sem e-mail principal'}{client.phone ? ` • ${formatPhone(client.phone)}` : ''}</p>
                        {client.extra_emails?.length ? <p className="text-sm text-slate-500">E-mails extras: {client.extra_emails.join(', ')}</p> : null}
                        <p className="text-sm text-slate-500">{[client.address_line, client.address_number, client.neighborhood, client.city, client.state].filter(Boolean).join(' • ') || 'Sem endereço cadastrado'}</p>
                      </div>
                      <div className="flex gap-2">
                        <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={() => handleEdit(client)}>
                          Editar
                        </Button>
                        <Button type="button" className="bg-red-600 hover:bg-red-700" onClick={() => handleDelete(client.id)}>
                          Excluir
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <Card>
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">{isEditing ? 'Editar cliente' : 'Cadastrar cliente'}</h3>
              <p className="text-sm text-slate-500">Com preenchimento automático de endereço via CEP.</p>
            </div>
            {isEditing && (
              <Button type="button" className="bg-slate-800 hover:bg-slate-900" onClick={resetForm}>
                Novo cadastro
              </Button>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Tipo</span>
                <select className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.type} onChange={(e) => handleChange('type', e.target.value)}>
                  {typeOptions.map((type) => <option key={type} value={type}>{type === 'pf' ? 'Pessoa física' : 'Pessoa jurídica'}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Status</span>
                <select className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.status} onChange={(e) => handleChange('status', e.target.value)}>
                  {statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </label>
              <label className="block md:col-span-2">
                <span className="mb-2 block text-sm font-medium text-slate-700">Nome completo / Razão social</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.name} onChange={(e) => handleChange('name', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">CPF / CNPJ</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.cpf_cnpj} onChange={(e) => handleChange('cpf_cnpj', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Telefone</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.phone} onChange={(e) => handleChange('phone', e.target.value)} />
              </label>
              <label className="block md:col-span-2">
                <span className="mb-2 block text-sm font-medium text-slate-700">E-mail principal</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.email} onChange={(e) => handleChange('email', e.target.value.toLowerCase())} />
              </label>
              {form.type === 'pj' && (
                <label className="block md:col-span-2">
                  <span className="mb-2 block text-sm font-medium text-slate-700">E-mails adicionais da empresa</span>
                  <textarea className="min-h-[90px] w-full rounded-xl border border-slate-300 px-4 py-3" value={form.extra_emails} onChange={(e) => handleChange('extra_emails', e.target.value)} placeholder="Separe por vírgula ou uma linha por e-mail" />
                </label>
              )}
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">CEP</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.zip_code} onChange={(e) => handleChange('zip_code', e.target.value)} onBlur={(e) => fillAddressFromCep(e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">UF</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.state} onChange={(e) => handleChange('state', e.target.value)} />
              </label>
              <label className="block md:col-span-2">
                <span className="mb-2 block text-sm font-medium text-slate-700">Logradouro</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_line} onChange={(e) => handleChange('address_line', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Número</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_number} onChange={(e) => handleChange('address_number', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Complemento</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_complement} onChange={(e) => handleChange('address_complement', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Bairro</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.neighborhood} onChange={(e) => handleChange('neighborhood', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-700">Cidade</span>
                <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.city} onChange={(e) => handleChange('city', e.target.value)} />
              </label>
              <label className="block md:col-span-2">
                <span className="mb-2 block text-sm font-medium text-slate-700">Observações</span>
                <textarea className="min-h-[96px] w-full rounded-xl border border-slate-300 px-4 py-3" value={form.notes} onChange={(e) => handleChange('notes', e.target.value)} />
              </label>
            </div>

            {lookingUpCep && <p className="text-sm text-slate-500">Consultando CEP...</p>}

            <div className="flex justify-end">
              <Button type="submit" disabled={saving || lookingUpCep}>
                {saving ? 'Salvando...' : isEditing ? 'Atualizar cliente' : 'Cadastrar cliente'}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </PageShell>
  );
}
