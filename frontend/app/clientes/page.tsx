'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Users, AlertTriangle, Building2, CheckCircle2, FileText, Wrench, CheckCircle, Clock, AlertCircle, Download, Plus, Trash2 } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid, FormSection, FormDivider } from '@/components/ui/form-field';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { apiFetch, API_URL } from '@/lib/api';
import { fetchAddressByCep } from '@/lib/cep';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';

type ClientStatus = 'ativo' | 'inativo' | 'inadimplente' | 'suspenso';
type ClientType = 'pf' | 'pj';
type ReviewStatus = 'enviado' | 'em_analise' | 'aprovado' | 'rejeitado' | 'reenvio_solicitado';

type ContactItem = { name: string; phone: string; email: string; role: string };

type Client = {
  id: number;
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
  email?: string | null;
  extra_emails?: string[] | null;
  phone?: string | null;
  contacts?: ContactItem[] | null;
  zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
  notes?: string | null;
  billing_day?: number | null;
};

type ClientDocument = {
  id: number;
  file_name: string;
  category: string;
  review_status: ReviewStatus;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

type VehicleSummary = { id: number; client_id: number; plate: string; status: string };

type TimelineContract = { id: number; start_date: string; plan_name?: string | null; status: string };
type TimelineOrder    = { id: number; number: string; type: string; status: string; executed_at?: string | null; scheduled_at?: string | null; created_at?: string | null; vehicle_plate?: string | null };
type TimelineBilling  = { id: number; amount: number; due_date: string; payment_date?: string | null; status: string; title?: string | null; plan_name?: string | null };

type TimelineEvent = {
  key: string;
  date: string;
  kind: 'contract' | 'os' | 'billing_paid' | 'billing_overdue' | 'billing_pending';
  title: string;
  subtitle: string;
};

function formatCurrency(value: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0);
}

function orderTypeLabel(type: string) {
  return ({ instalacao: 'Instalação', manutencao: 'Manutenção', retirada: 'Retirada', visita_tecnica: 'Visita técnica' } as Record<string, string>)[type] ?? type;
}

const emptyContact: ContactItem = { name: '', phone: '', email: '', role: '' };

type ClientFormState = {
  name: string;
  cpf_cnpj: string;
  type: ClientType;
  status: ClientStatus;
  email: string;
  extra_emails: string;
  phone: string;
  contacts: ContactItem[];
  zip_code: string;
  address_line: string;
  address_number: string;
  address_complement: string;
  neighborhood: string;
  city: string;
  state: string;
  notes: string;
  billing_day: string;
};

const initialForm: ClientFormState = {
  name: '',
  cpf_cnpj: '',
  type: 'pf',
  status: 'ativo',
  email: '',
  extra_emails: '',
  phone: '',
  contacts: [],
  zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
  notes: '',
  billing_day: '',
};

const documentCategoryOptions = ['cnh', 'rg', 'cpf', 'contrato', 'comprovante_endereco', 'cartao_cnpj', 'contrato_social', 'outro'];
const fileInputClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white file:mr-4 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white dark:file:bg-brand-500';

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function parseExtraEmails(value: string) {
  return value.split(/[,;\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
}

function cardKeyHandler(event: React.KeyboardEvent<HTMLElement>, callback: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    callback();
  }
}

export default function ClientesPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [clients, setClients] = useState<Client[]>([]);
  const [vehicleSummaries, setVehicleSummaries] = useState<VehicleSummary[]>([]);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [clientDocuments, setClientDocuments] = useState<ClientDocument[]>([]);
  const [form, setForm] = useState<ClientFormState>(initialForm);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<'cadastro' | 'historico' | 'documentos'>('cadastro');
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [docCategory, setDocCategory] = useState('cnh');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [clientTimeline, setClientTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);

  async function loadClients(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (typeFilter) query.set('type', typeFilter);
      query.set('limit', '200');
      const [clientResponse, vehicleResponse] = await Promise.all([
        apiFetch<Client[]>(`/clients?${query.toString()}`, {}, currentToken),
        apiFetch<VehicleSummary[]>('/vehicles?limit=500', {}, currentToken),
      ]);
      setClients(clientResponse);
      setVehicleSummaries(vehicleResponse);
      if (selectedClient) {
        const refreshed = clientResponse.find((item) => item.id === selectedClient.id) || null;
        setSelectedClient(refreshed);
      }
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadClientDocuments(currentToken: string, clientId: number) {
    try {
      const response = await apiFetch<ClientDocument[]>(`/clients/${clientId}/documents`, {}, currentToken);
      setClientDocuments(response);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function loadClientTimeline(currentToken: string, clientId: number) {
    setTimelineLoading(true);
    try {
      const [contracts, orders, bills] = await Promise.allSettled([
        apiFetch<TimelineContract[]>(`/contracts?client_id=${clientId}`, {}, currentToken),
        apiFetch<TimelineOrder[]>(`/service-orders?client_id=${clientId}&limit=30`, {}, currentToken),
        apiFetch<TimelineBilling[]>(`/billings?client_id=${clientId}&limit=30`, {}, currentToken),
      ]);

      const events: TimelineEvent[] = [];

      if (contracts.status === 'fulfilled') {
        for (const c of contracts.value) {
          events.push({
            key: `contract-${c.id}`,
            date: c.start_date,
            kind: 'contract',
            title: `Contrato iniciado — ${c.plan_name || 'Plano'}`,
            subtitle: `Status: ${c.status}`,
          });
        }
      }

      if (orders.status === 'fulfilled') {
        for (const o of orders.value) {
          events.push({
            key: `os-${o.id}`,
            date: o.executed_at || o.scheduled_at || o.created_at || '',
            kind: 'os',
            title: `OS #${o.number} — ${orderTypeLabel(o.type)}`,
            subtitle: o.vehicle_plate ? `Veículo: ${o.vehicle_plate} • ${o.status}` : `Status: ${o.status}`,
          });
        }
      }

      if (bills.status === 'fulfilled') {
        for (const b of bills.value) {
          const kind: TimelineEvent['kind'] =
            b.status === 'paga' ? 'billing_paid' :
            b.status === 'vencida' ? 'billing_overdue' : 'billing_pending';
          events.push({
            key: `billing-${b.id}`,
            date: b.payment_date || b.due_date,
            kind,
            title: `${b.title || b.plan_name || 'Cobrança'} — ${formatCurrency(b.amount)}`,
            subtitle: b.payment_date ? `Pago em ${b.payment_date}` : `Venc. ${b.due_date}`,
          });
        }
      }

      const sorted = events
        .filter((e) => e.date)
        .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
        .slice(0, 25);

      setClientTimeline(sorted);
    } catch {
      setClientTimeline([]);
    } finally {
      setTimelineLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadClients(token);
  }, [token]);

  useEffect(() => {
    if (!token || !selectedClient) {
      setClientDocuments([]);
      setClientTimeline([]);
      return;
    }
    loadClientDocuments(token, selectedClient.id);
    loadClientTimeline(token, selectedClient.id);
  }, [token, selectedClient?.id]);

  const stats = useMemo(() => ({
    total: clients.length,
    active: clients.filter((item) => item.status === 'ativo').length,
    delinquent: clients.filter((item) => item.status === 'inadimplente').length,
    company: clients.filter((item) => item.type === 'pj').length,
  }), [clients]);

  const vehiclesByClient = useMemo(() => vehicleSummaries.reduce<Record<number, VehicleSummary[]>>((acc, vehicle) => {
    if (!acc[vehicle.client_id]) acc[vehicle.client_id] = [];
    acc[vehicle.client_id].push(vehicle);
    return acc;
  }, {}), [vehicleSummaries]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
    setDocFiles([]);
    setDocCategory('cnh');
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(client: Client) {
    setSelectedClient(client);
    setIsEditing(true);
    setModalError('');
    setForm({
      name: client.name || '',
      cpf_cnpj: formatCpfCnpj(client.cpf_cnpj || ''),
      type: client.type || 'pf',
      status: client.status || 'ativo',
      email: client.email || '',
      extra_emails: (client.extra_emails || []).join('\n'),
      phone: client.phone ? formatPhone(client.phone) : '',
      contacts: (client.contacts || []).map((c) => ({ name: c.name || '', phone: c.phone || '', email: c.email || '', role: c.role || '' })),
      zip_code: client.zip_code ? formatZipCode(client.zip_code) : '',
      address_line: client.address_line || '',
      address_number: client.address_number || '',
      address_complement: client.address_complement || '',
      neighborhood: client.neighborhood || '',
      city: client.city || '',
      state: client.state || '',
      notes: client.notes || '',
      billing_day: client.billing_day != null ? String(client.billing_day) : '',
    });
    setDocFiles([]);
    setModalOpen(true);
  }

  function addContact() {
    setForm((prev) => ({ ...prev, contacts: [...prev.contacts, { ...emptyContact }] }));
  }

  function removeContact(index: number) {
    setForm((prev) => ({ ...prev, contacts: prev.contacts.filter((_, i) => i !== index) }));
  }

  function updateContact(index: number, field: keyof ContactItem, value: string) {
    setForm((prev) => {
      const contacts = [...prev.contacts];
      contacts[index] = { ...contacts[index], [field]: value };
      return { ...prev, contacts };
    });
  }

  async function downloadTimelinePdf() {
    if (!token || !selectedClient) return;
    try {
      const response = await fetch(`${API_URL.replace(/\/+$/, '')}/clients/${selectedClient.id}/timeline-pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Erro ao gerar PDF');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `timeline-${selectedClient.name}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(parseError(err));
    }
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
      const address = await fetchAddressByCep(cep);
      if (address) {
        setForm((prev) => ({
          ...prev,
          zip_code: formatZipCode(cep),
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

  async function submitClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setModalError('');
    setFeedback('');
    try {
      const cleanContacts = form.contacts
        .filter((c) => c.name.trim())
        .map((c) => ({ name: c.name.trim(), phone: c.phone.trim() || null, email: c.email.trim() || null, role: c.role.trim() || null }));

      const payload = {
        name: form.name.trim(),
        cpf_cnpj: onlyDigits(form.cpf_cnpj),
        type: form.type,
        status: form.status,
        email: normalizeEmail(form.email) || null,
        extra_emails: parseExtraEmails(form.extra_emails),
        phone: onlyDigits(form.phone) || null,
        contacts: cleanContacts.length ? cleanContacts : null,
        zip_code: onlyDigits(form.zip_code) || null,
        address_line: form.address_line.trim() || null,
        address_number: form.address_number.trim() || null,
        address_complement: form.address_complement.trim() || null,
        neighborhood: form.neighborhood.trim() || null,
        city: form.city.trim() || null,
        state: form.state.trim() || null,
        notes: form.notes.trim() || null,
        billing_day: form.billing_day ? Number(form.billing_day) : null,
      };

      const saved = isEditing && selectedClient
        ? await apiFetch<Client>(`/clients/${selectedClient.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<Client>('/clients', { method: 'POST', body: JSON.stringify(payload) }, token);

      if (docFiles.length) {
        const body = new FormData();
        body.append('category', docCategory);
        docFiles.forEach((file) => body.append('files', file));
        await apiFetch(`/clients/${saved.id}/documents`, { method: 'POST', body }, token);
      }

      setFeedback(isEditing ? 'Cliente atualizado com sucesso.' : 'Cliente cadastrado com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadClients(token);
      setSelectedClient(saved);
      await loadClientDocuments(token, saved.id);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function uploadDocuments() {
    if (!token || !selectedClient || !canEdit || !docFiles.length) return;
    setUploading(true);
    setError('');
    setFeedback('');
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/clients/${selectedClient.id}/documents`, { method: 'POST', body }, token);
      setFeedback('Documentos enviados com sucesso.');
      setDocFiles([]);
      await loadClientDocuments(token, selectedClient.id);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function deleteDocument(documentId: number) {
    if (!token || !selectedClient || !canEdit) return;
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/clients/${selectedClient.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      await loadClientDocuments(token, selectedClient.id);
      setFeedback('Documento removido com sucesso.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function reviewDocument(documentId: number, status: ReviewStatus) {
    if (!token || !selectedClient || !canEdit) return;
    const notes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/clients/${selectedClient.id}/documents/${documentId}/review`, {
        method: 'POST',
        body: JSON.stringify({ review_status: status, review_notes: notes || null }),
      }, token);
      await loadClientDocuments(token, selectedClient.id);
      setFeedback('Status do documento atualizado.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  return (
    <PageShell title="Clientes" description="Gestão da base cadastral com formulário em modal, documentação centralizada e visão rápida dos veículos vinculados.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Clientes cadastrados" value={stats.total}      hint="Base total disponível"           icon={<Users className="h-5 w-5" />} />
        <StatCard label="Clientes ativos"      value={stats.active}     hint="Cadastros em operação"  tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
        <StatCard label="Inadimplentes"        value={stats.delinquent} hint="Exigem ação do financeiro" tone="warning" icon={<AlertTriangle className="h-5 w-5" />} />
        <StatCard label="Empresas (PJ)"        value={stats.company}    hint="Cadastros PJ na base"    tone="brand"   icon={<Building2 className="h-5 w-5" />} />
      </section>

      <section className="mt-6">
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Base de clientes"
            description="Pesquise e gerencie a carteira completa de clientes."
            actions={canEdit ? <Button type="button" onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button> : null}
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <Input placeholder="Buscar por nome, CPF/CNPJ ou e-mail" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
            <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-44">
              <option value="">Todos os status</option>
              <option value="ativo">Ativo</option>
              <option value="inativo">Inativo</option>
              <option value="inadimplente">Inadimplente</option>
              <option value="suspenso">Suspenso</option>
            </Select>
            <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-40">
              <option value="">Todos os tipos</option>
              <option value="pf">Pessoa física</option>
              <option value="pj">Pessoa jurídica</option>
            </Select>
            <Button type="button" variant="secondary" onClick={() => token && loadClients(token)} disabled={loading}>
              {loading ? 'Atualizando…' : 'Filtrar'}
            </Button>
          </div>

          <div className="mt-4">
            {loading ? (
              <TableSkeleton rows={7} cols={5} />
            ) : clients.length === 0 ? (
              <EmptyState icon={Users} title="Nenhum cliente encontrado" description="Ajuste os filtros ou cadastre o primeiro cliente." action={canEdit ? <Button onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button> : undefined} />
            ) : (
              <Table>
                <TableHead>
                  <Th>Cliente</Th>
                  <Th>Documento</Th>
                  <Th>Contato</Th>
                  <Th>Status</Th>
                  <Th className="w-40" />
                </TableHead>
                <TableBody>
                  {clients.map((client) => {
                    const vehicles = vehiclesByClient[client.id] || [];
                    return (
                      <Tr key={client.id}>
                        <Td>
                          <p className="font-medium text-slate-900 dark:text-white">{client.name}</p>
                          <p className="text-xs text-slate-400">{client.type === 'pj' ? 'Pessoa Jurídica' : 'Pessoa Física'} · {vehicles.length} veículo(s)</p>
                        </Td>
                        <Td className="font-mono text-xs">{formatCpfCnpj(client.cpf_cnpj)}</Td>
                        <Td>
                          <p className="text-sm">{client.email || '—'}</p>
                          <p className="text-xs text-slate-400">{client.phone ? formatPhone(client.phone) : ''}</p>
                        </Td>
                        <Td>
                          <Badge variant={statusVariant(client.status)}>{statusLabel(client.status)}</Badge>
                        </Td>
                        <Td>
                          <div className="flex justify-end gap-1.5">
                            <Button
                              type="button"
                              variant="secondary"
                              onClick={() => { setSelectedClient(client); setDetailsOpen(true); setDetailsTab('cadastro'); }}
                              className="px-3 py-1.5 text-xs"
                            >
                              Detalhes
                            </Button>
                            {canEdit && (
                              <Button
                                type="button"
                                variant="secondary"
                                onClick={() => openEditModal(client)}
                                className="px-3 py-1.5 text-xs"
                              >
                                Editar
                              </Button>
                            )}
                          </div>
                        </Td>
                      </Tr>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </div>
        </Card>
      </section>

      {/* Modal de detalhes */}
      <Modal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedClient(null); }}
        title={selectedClient?.name ?? ''}
        subtitle="Detalhes do cliente"
        size="xl"
      >
        {selectedClient && (
          <div className="space-y-4">
            {/* Abas */}
            <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
              {(['cadastro', 'historico', 'documentos'] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setDetailsTab(tab)}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${detailsTab === tab ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                  {tab === 'cadastro' ? 'Cadastro' : tab === 'historico' ? 'Histórico' : 'Documentos'}
                </button>
              ))}
            </div>

            {/* Aba Cadastro */}
            {detailsTab === 'cadastro' && (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Documento</p>
                    <p className="mt-2 font-semibold text-slate-900 dark:text-white">{formatCpfCnpj(selectedClient.cpf_cnpj)}</p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Status</p>
                    <div className="mt-2"><Badge variant={statusVariant(selectedClient.status)}>{statusLabel(selectedClient.status)}</Badge></div>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">E-mail</p>
                    <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{selectedClient.email || 'Não informado'}</p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Telefone</p>
                    <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{selectedClient.phone ? formatPhone(selectedClient.phone) : 'Não informado'}</p>
                  </div>
                  <div className="rounded-xl border border-brand-100 bg-brand-50/60 p-4 dark:border-brand-900/40 dark:bg-brand-950/30">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-brand-500 dark:text-brand-400">Dia de vencimento</p>
                    <p className="mt-2 text-sm font-semibold text-brand-800 dark:text-brand-200">
                      {selectedClient.billing_day ? `Todo dia ${selectedClient.billing_day}` : 'Não configurado'}
                    </p>
                    <p className="mt-0.5 text-xs text-brand-500/70 dark:text-brand-400/60">Padrão para novos contratos</p>
                  </div>
                </div>
                <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Endereço</p>
                  <p className="mt-2 text-sm leading-6 text-slate-700 dark:text-slate-300">
                    {[selectedClient.address_line, selectedClient.address_number, selectedClient.neighborhood, selectedClient.city, selectedClient.state].filter(Boolean).join(', ') || 'Não informado'}
                  </p>
                </div>
                {(selectedClient.contacts || []).length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Contatos adicionais</p>
                    <div className="space-y-2">
                      {(selectedClient.contacts || []).map((c, i) => (
                        <div key={i} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/50">
                          <p className="font-medium text-slate-900 dark:text-white">{c.name}{c.role && <span className="ml-2 text-xs font-normal text-slate-400">({c.role})</span>}</p>
                          <p className="mt-0.5 text-xs text-slate-500">{[c.phone && formatPhone(c.phone), c.email].filter(Boolean).join(' · ')}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Veículos vinculados</p>
                  {(vehiclesByClient[selectedClient.id] || []).length === 0 ? (
                    <p className="text-sm text-slate-400">Nenhum veículo vinculado.</p>
                  ) : (
                    <div className="space-y-1.5">
                      {(vehiclesByClient[selectedClient.id] || []).map((v) => (
                        <div key={v.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900/50">
                          <span className="font-medium text-slate-900 dark:text-white">{v.plate}</span>
                          <Badge variant={statusVariant(v.status)}>{statusLabel(v.status)}</Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Aba Histórico */}
            {detailsTab === 'historico' && (
              <div>
                <div className="mb-3 flex justify-end">
                  <Button type="button" variant="secondary" onClick={downloadTimelinePdf} className="gap-1.5">
                    <Download className="h-4 w-4" /> Exportar PDF
                  </Button>
                </div>
                {timelineLoading ? (
                  <div className="space-y-3">
                    {[1,2,3].map((i) => <div key={i} className="flex gap-3"><div className="h-7 w-7 shrink-0 animate-pulse rounded-full bg-slate-100 dark:bg-slate-800" /><div className="flex-1 space-y-1.5 pt-1"><div className="h-3 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-800" /><div className="h-3 w-1/2 animate-pulse rounded bg-slate-100 dark:bg-slate-800" /></div></div>)}
                  </div>
                ) : clientTimeline.length === 0 ? (
                  <p className="text-sm text-slate-400">Nenhum evento registrado.</p>
                ) : (
                  <ol className="relative border-l border-slate-200 dark:border-slate-700">
                    {clientTimeline.map((event) => {
                      const cfg = {
                        contract:        { bg: 'bg-brand-100 dark:bg-brand-900/50',   icon: <FileText className="h-3.5 w-3.5 text-brand-600 dark:text-brand-300" /> },
                        os:              { bg: 'bg-slate-100 dark:bg-slate-800',        icon: <Wrench className="h-3.5 w-3.5 text-slate-600 dark:text-slate-300" /> },
                        billing_paid:    { bg: 'bg-emerald-100 dark:bg-emerald-900/50', icon: <CheckCircle className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-300" /> },
                        billing_overdue: { bg: 'bg-rose-100 dark:bg-rose-900/50',       icon: <AlertCircle className="h-3.5 w-3.5 text-rose-600 dark:text-rose-300" /> },
                        billing_pending: { bg: 'bg-amber-100 dark:bg-amber-900/50',     icon: <Clock className="h-3.5 w-3.5 text-amber-600 dark:text-amber-300" /> },
                      }[event.kind];
                      return (
                        <li key={event.key} className="mb-4 ml-5">
                          <span className={`absolute -left-[14px] flex h-7 w-7 items-center justify-center rounded-full ring-2 ring-white dark:ring-slate-950 ${cfg.bg}`}>{cfg.icon}</span>
                          <div className="rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/60">
                            <p className="text-xs font-semibold text-slate-900 dark:text-white">{event.title}</p>
                            <p className="mt-0.5 text-xs text-slate-500">{event.subtitle}</p>
                            <time className="mt-1 block text-[10px] text-slate-400">{event.date}</time>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
            )}

            {/* Aba Documentos */}
            {detailsTab === 'documentos' && (
              <div className="space-y-4">
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <Select value={docCategory} onChange={(e) => setDocCategory(e.target.value)} className="w-44">
                      {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </Select>
                    <input type="file" multiple className={fileInputClass} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                    <Button type="button" disabled={uploading || !docFiles.length} onClick={uploadDocuments}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
                  </div>
                )}
                {clientDocuments.length === 0 ? (
                  <EmptyState icon={FileText} title="Nenhum documento" description="Nenhum documento foi anexado a este cliente." />
                ) : (
                  <div className="space-y-3">
                    {clientDocuments.map((doc) => (
                      <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                            <p className="mt-0.5 text-xs text-slate-400">Categoria: {doc.category}</p>
                            {doc.review_notes && <p className="mt-0.5 text-xs text-slate-400">Obs.: {doc.review_notes}</p>}
                          </div>
                          <Badge variant={statusVariant(doc.review_status)}>{statusLabel(doc.review_status)}</Badge>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Visualizar</a>
                          <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Baixar</a>
                          {canEdit && (
                            <>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                              <button type="button" onClick={() => deleteDocument(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={modalOpen}
        onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }}
        title={isEditing ? 'Editar cliente' : 'Novo cliente'}
        description="Preencha os dados principais do cadastro. Você também pode incluir documentação inicial no mesmo fluxo."
        size="xl"
      >
        <form className="space-y-6" onSubmit={submitClient}>
          {modalError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
              {modalError}
            </div>
          )}

          <FormSection title="Dados principais">
            <FormGrid cols={2}>
              <FormField label="Nome / razão social" required>
                <Input placeholder="Nome completo ou razão social" value={form.name} onChange={(e) => handleChange('name', e.target.value)} required />
              </FormField>
              <FormGrid cols={2}>
                <FormField label="Tipo de pessoa" required>
                  <Select value={form.type} onChange={(e) => handleChange('type', e.target.value)}>
                    <option value="pf">Pessoa física</option>
                    <option value="pj">Pessoa jurídica</option>
                  </Select>
                </FormField>
                <FormField label="Status" required>
                  <Select value={form.status} onChange={(e) => handleChange('status', e.target.value)}>
                    <option value="ativo">Ativo</option>
                    <option value="inativo">Inativo</option>
                    <option value="inadimplente">Inadimplente</option>
                    <option value="suspenso">Suspenso</option>
                  </Select>
                </FormField>
              </FormGrid>
              <FormField label={form.type === 'pj' ? 'CNPJ' : 'CPF'} required>
                <Input placeholder={form.type === 'pj' ? '00.000.000/0001-00' : '000.000.000-00'} value={form.cpf_cnpj} onChange={(e) => handleChange('cpf_cnpj', e.target.value)} required />
              </FormField>
              <FormField label="E-mail principal" required>
                <Input type="email" placeholder="email@empresa.com" value={form.email} onChange={(e) => handleChange('email', e.target.value)} required />
              </FormField>
              <FormField label="Telefone principal">
                <Input placeholder="(11) 99999-0000" value={form.phone} onChange={(e) => handleChange('phone', e.target.value)} />
              </FormField>
            </FormGrid>
            <FormField label="E-mails adicionais" hint="Um por linha ou separados por vírgula">
              <Textarea placeholder="outro@email.com, terceiro@email.com" value={form.extra_emails} onChange={(e) => handleChange('extra_emails', e.target.value)} className="min-h-[72px]" />
            </FormField>
          </FormSection>

          <FormDivider />

          <FormSection title="Contatos adicionais">
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400 dark:text-slate-500">Responsáveis, técnicos ou gestores adicionais.</p>
              <Button type="button" variant="secondary" onClick={addContact} className="gap-1.5">
                <Plus className="h-3.5 w-3.5" /> Adicionar contato
              </Button>
            </div>
            {form.contacts.map((contact, i) => (
              <div key={i} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/50 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
                <Input placeholder="Nome" value={contact.name} onChange={(e) => updateContact(i, 'name', e.target.value)} />
                <Input placeholder="Telefone" value={contact.phone} onChange={(e) => updateContact(i, 'phone', formatPhone(e.target.value))} />
                <Input placeholder="E-mail" value={contact.email} onChange={(e) => updateContact(i, 'email', e.target.value)} />
                <Input placeholder="Cargo" value={contact.role} onChange={(e) => updateContact(i, 'role', e.target.value)} />
                <button type="button" onClick={() => removeContact(i)} className="flex items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-3 text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </FormSection>

          <FormDivider />

          <FormSection title="Endereço">
            <div className="flex gap-2">
              <FormField label="CEP" className="w-40">
                <Input placeholder="00000-000" value={form.zip_code} onChange={(e) => handleChange('zip_code', e.target.value)} onBlur={(e) => fillAddressFromCep(e.target.value)} />
              </FormField>
              <div className="flex items-end">
                <Button type="button" variant="secondary" onClick={() => fillAddressFromCep(form.zip_code)} disabled={lookingUpCep}>
                  {lookingUpCep ? 'Buscando…' : 'Buscar CEP'}
                </Button>
              </div>
            </div>
            <FormGrid cols={3}>
              <FormField label="Logradouro" className="col-span-2">
                <Input placeholder="Rua / Avenida" value={form.address_line} onChange={(e) => handleChange('address_line', e.target.value)} />
              </FormField>
              <FormField label="Número">
                <Input placeholder="Nº" value={form.address_number} onChange={(e) => handleChange('address_number', e.target.value)} />
              </FormField>
              <FormField label="Complemento">
                <Input placeholder="Apto, sala…" value={form.address_complement} onChange={(e) => handleChange('address_complement', e.target.value)} />
              </FormField>
              <FormField label="Bairro">
                <Input placeholder="Bairro" value={form.neighborhood} onChange={(e) => handleChange('neighborhood', e.target.value)} />
              </FormField>
              <FormField label="Cidade">
                <Input placeholder="Cidade" value={form.city} onChange={(e) => handleChange('city', e.target.value)} />
              </FormField>
              <FormField label="UF">
                <Input placeholder="SP" value={form.state} onChange={(e) => handleChange('state', e.target.value)} maxLength={2} />
              </FormField>
            </FormGrid>
            <FormField
              label="Dia de vencimento preferido"
              hint="1 a 28 — usado como padrão ao gerar contratos e cobranças para este cliente"
            >
              <Input
                type="number"
                min={1}
                max={28}
                placeholder="Ex.: 20"
                value={form.billing_day}
                onChange={(e) => setForm((p) => ({ ...p, billing_day: e.target.value.slice(0, 2) }))}
                className="max-w-[120px]"
              />
            </FormField>
            <FormField label="Observações">
              <Textarea placeholder="Anotações administrativas internas" value={form.notes} onChange={(e) => handleChange('notes', e.target.value)} />
            </FormField>
          </FormSection>

          <FormDivider />

          <FormSection title="Documentação inicial (opcional)">
            <p className="text-xs text-slate-400 dark:text-slate-500">Arquivos enviados automaticamente após salvar o cliente.</p>
            <FormGrid cols={2}>
              <FormField label="Categoria">
                <Select value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
                  {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              </FormField>
              <FormField label="Arquivo(s)">
                <input type="file" multiple className={fileInputClass} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
              </FormField>
            </FormGrid>
          </FormSection>

          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Button type="button" variant="secondary" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</Button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando…' : isEditing ? 'Atualizar cliente' : 'Cadastrar cliente'}</Button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}
