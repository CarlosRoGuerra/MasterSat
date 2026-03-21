'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { clearSession, getAccessToken } from '@/lib/auth';

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

function onlyDigits(value: string) {
  return value.replace(/\D/g, '');
}

function formatCpfCnpj(value: string) {
  const digits = onlyDigits(value).slice(0, 14);
  if (digits.length <= 11) {
    return digits
      .replace(/(\d{3})(\d)/, '$1.$2')
      .replace(/(\d{3})(\d)/, '$1.$2')
      .replace(/(\d{3})(\d{1,2})$/, '$1-$2');
  }
  return digits
    .replace(/^(\d{2})(\d)/, '$1.$2')
    .replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3')
    .replace(/\.(\d{3})(\d)/, '.$1/$2')
    .replace(/(\d{4})(\d)/, '$1-$2');
}

function formatPhone(value: string) {
  const digits = onlyDigits(value).slice(0, 11);
  if (digits.length <= 10) {
    return digits
      .replace(/^(\d{2})(\d)/g, '($1) $2')
      .replace(/(\d{4})(\d)/, '$1-$2');
  }
  return digits
    .replace(/^(\d{2})(\d)/g, '($1) $2')
    .replace(/(\d{5})(\d)/, '$1-$2');
}

function formatCep(value: string) {
  const digits = onlyDigits(value).slice(0, 8);
  return digits.replace(/(\d{5})(\d)/, '$1-$2');
}

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
  const [token, setToken] = useState('');
  const [clients, setClients] = useState<Client[]>([]);
  const [form, setForm] = useState<ClientFormState>(initialForm);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    const currentToken = getAccessToken();
    if (!currentToken) {
      window.location.href = '/login/admin';
      return;
    }
    setToken(currentToken);
  }, []);

  async function loadClients(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<Client[]>('/clients', {}, currentToken);
      setClients(response);
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
    loadClients(token);
  }, [token]);

  const filteredClients = useMemo(() => {
    const term = search.trim().toLowerCase();
    return clients.filter((client) => {
      const matchesSearch = !term || [
        client.name,
        client.cpf_cnpj,
        client.email || '',
        client.phone || '',
        client.city || '',
      ].some((value) => value.toLowerCase().includes(term));
      const matchesStatus = !statusFilter || client.status === statusFilter;
      const matchesType = !typeFilter || client.type === typeFilter;
      return matchesSearch && matchesStatus && matchesType;
    });
  }, [clients, search, statusFilter, typeFilter]);

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
      zip_code: formatCep(client.zip_code || ''),
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
    if (field === 'zip_code') nextValue = formatCep(value);
    if (field === 'state') nextValue = value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
    setForm((prev) => ({ ...prev, [field]: nextValue }));
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
    if (!window.confirm('Deseja realmente excluir este cliente?')) return;
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
      {(error || feedback) && (
        <div className="mb-6 space-y-3">
          {error && <p className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="space-y-6">
          <Card>
            <div className="mb-4 flex flex-wrap items-end gap-3">
              <div className="min-w-[220px] flex-1">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Busca</label>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nome, documento, e-mail, telefone ou cidade" className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div className="min-w-[160px]">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Status</label>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div className="min-w-[140px]">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todos</option>
                  {typeOptions.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <Button type="button" onClick={() => token && loadClients(token)} disabled={loading}>Atualizar</Button>
                <button type="button" onClick={() => { setSearch(''); setStatusFilter(''); setTypeFilter(''); }} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Limpar</button>
              </div>
            </div>

            <div className="mb-3 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Clientes cadastrados</h3>
                <p className="text-sm text-slate-500">Gestão administrativa completa de clientes.</p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{filteredClients.length} registro(s)</span>
            </div>

            <div className="space-y-3">
              {loading ? (
                <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-sm text-slate-500">Carregando clientes...</p>
              ) : filteredClients.length ? (
                filteredClients.map((client) => (
                  <div key={client.id} className="rounded-2xl border border-slate-200 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-base font-semibold text-slate-900">{client.name}</p>
                        <p className="text-sm text-slate-600">{formatCpfCnpj(client.cpf_cnpj)} • {client.type.toUpperCase()} • {client.status}</p>
                        <p className="text-sm text-slate-500">{client.email || 'Sem e-mail'} • {client.phone ? formatPhone(client.phone) : 'Sem telefone'}</p>
                        {client.type === 'pj' && client.extra_emails?.length ? <p className="text-xs text-slate-500">E-mails extras: {client.extra_emails.join(', ')}</p> : null}
                        <p className="text-sm text-slate-500">{client.city ? `${client.city}/${client.state || '--'}` : 'Cidade não informada'}</p>
                      </div>
                      <div className="flex gap-2">
                        <button type="button" onClick={() => handleEdit(client)} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Editar</button>
                        <button type="button" onClick={() => handleDelete(client.id)} className="rounded-xl border border-red-200 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50">Excluir</button>
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-sm text-slate-500">Nenhum cliente encontrado com os filtros atuais.</p>
              )}
            </div>
          </Card>
        </div>

        <Card>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">{isEditing ? 'Editar cliente' : 'Novo cliente'}</h3>
              <p className="text-sm text-slate-500">Cadastro completo para o administrativo.</p>
            </div>
            {isEditing && (
              <button type="button" onClick={resetForm} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
                Cancelar edição
              </button>
            )}
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Nome</label>
              <input value={form.name} onChange={(e) => handleChange('name', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" required />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">CPF/CNPJ</label>
                <input value={form.cpf_cnpj} onChange={(e) => handleChange('cpf_cnpj', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Telefone</label>
                <input value={form.phone} onChange={(e) => handleChange('phone', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Tipo</label>
                <select value={form.type} onChange={(e) => handleChange('type', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  {typeOptions.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Status</label>
                <select value={form.status} onChange={(e) => handleChange('status', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm">
                  {statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">CEP</label>
                <input value={form.zip_code} onChange={(e) => handleChange('zip_code', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">E-mail</label>
              <input type="email" value={form.email} onChange={(e) => handleChange('email', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
            </div>
            {form.type === 'pj' && (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">E-mails adicionais</label>
                <textarea value={form.extra_emails} onChange={(e) => handleChange('extra_emails', e.target.value)} className="min-h-[96px] w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" placeholder="Separe por vírgula ou uma linha por e-mail" />
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Endereço</label>
                <input value={form.address_line} onChange={(e) => handleChange('address_line', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Número</label>
                <input value={form.address_number} onChange={(e) => handleChange('address_number', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Complemento</label>
                <input value={form.address_complement} onChange={(e) => handleChange('address_complement', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Bairro</label>
                <input value={form.neighborhood} onChange={(e) => handleChange('neighborhood', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Cidade</label>
                <input value={form.city} onChange={(e) => handleChange('city', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">UF</label>
                <input value={form.state} onChange={(e) => handleChange('state', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" maxLength={2} />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Endereço completo livre</label>
                <input value={form.address} onChange={(e) => handleChange('address', e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Observações</label>
              <textarea value={form.notes} onChange={(e) => handleChange('notes', e.target.value)} className="min-h-[96px] w-full rounded-xl border border-slate-300 px-3 py-2 text-sm" />
            </div>

            <Button type="submit" disabled={saving} className="w-full">
              {saving ? 'Salvando...' : isEditing ? 'Salvar alterações' : 'Cadastrar cliente'}
            </Button>
          </form>
        </Card>
      </div>
    </PageShell>
  );
}
