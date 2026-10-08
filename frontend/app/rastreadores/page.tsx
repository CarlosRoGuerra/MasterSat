'use client';

import { FormEvent, Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle, Radio, CheckCircle2, Package, Wrench } from 'lucide-react';

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
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { BillingDayInput, erroDiaVencimento } from '@/components/ui/billing-day-input';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import { apiFetch, apiFetchAll, apiFetchList } from '@/lib/api';
import { onlyDigits, formatCpfCnpj, formatDate, pricePeriodSuffix } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { useAssistantContextActions } from '@/lib/assistant-context';
import type { TrackerStatus, ClientOption, VehicleOption } from '@/lib/domain-types';
import { AcoesEmLote, type AcaoLote, type AcaoLoteCorpo, type AcaoLoteResultado } from './_components/acoes-em-lote';

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

type ManufacturerOption = { code: string; description: string };
type PlanOption = { id: number; name: string; price: number; active?: boolean; billing_interval_months?: number };

type LoteItem = {
  imei: string;
  situacao: 'criado' | 'ja_existe' | 'repetido_no_lote' | 'invalido';
  motivo?: string | null;
  tracker_id?: number | null;
};
type LoteResultado = {
  simulacao: boolean;
  total_enviados: number;
  criados: number;
  ignorados: number;
  itens: LoteItem[];
};
type TrackerHistory = { id: number; action: string; previous_vehicle_id?: number | null; new_vehicle_id?: number | null; previous_client_id?: number | null; new_client_id?: number | null; new_status?: string | null; event_date?: string | null; notes?: string | null; created_at?: string | null };
type ContractInfo = { id: number; tracker_id?: number | null; vehicle_id?: number | null; plan_id: number; plan_name?: string | null; status: string; monthly_value?: number | null; start_date?: string | null; next_due_date?: string | null; open_billings?: number };

/** Resposta do envio completo para a Multiportal. */
type TrackerLinkFlow = {
  overall_success: boolean;
  steps: { friendly_title?: string | null; friendly_message?: string | null; success: boolean }[];
};

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
};

type DiaSugerido = {
  dia: number | null;
  origem: 'cliente' | 'contrato_ativo' | 'contrato_anterior' | null;
  contrato_id: number | null;
  alternativas: number[];
};

function origemDia(s: DiaSugerido) {
  if (s.origem === 'cliente') return 'herdado do cadastro do cliente';
  if (s.origem === 'contrato_ativo') return `herdado do contrato #${s.contrato_id}`;
  return `herdado do contrato anterior #${s.contrato_id}`;
}

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
};

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-400 dark:focus:border-brand-400';
const areaClass = `${fieldClass} min-h-[88px] resize-y`;
const statusOptions: TrackerStatus[] = ['em_estoque', 'instalado', 'em_manutencao', 'extraviado', 'descartado'];

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

function integrationVariant(status?: string | null): 'success' | 'danger' | 'warning' | 'default' {
  if (status === 'sincronizado') return 'success';
  if (status === 'erro') return 'danger';
  if (status === 'pendente') return 'warning';
  return 'default';
}

function integrationLabel(status?: string | null): string {
  const map: Record<string, string> = {
    sincronizado: 'Sincronizado',
    erro: 'Erro de sync',
    pendente: 'Sync pendente',
    sem_vinculo_externo: 'Sem vínculo externo',
  };
  return (status && map[status]) || 'Sem sync';
}

function friendlyAction(value: string) {
  const map: Record<string, string> = {
    created: 'Cadastro inicial',
    linked: 'Vínculo atualizado',
    contract_created: 'Contrato criado',
    client_changed: 'Cliente alterado',
    unlinked: 'Desvínculo',
    swapped_out: 'Substituído (retornou ao estoque)',
    swapped_in: 'Instalado em substituição',
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
  error,
  canEdit,
  onDetails,
  onEdit,
  selecionados,
  onToggle,
  onToggleTodos,
}: {
  trackers: Tracker[];
  loading: boolean;
  error?: string;
  canEdit: boolean;
  onDetails: (t: Tracker) => void;
  onEdit: (t: Tracker) => void;
  selecionados: Set<number>;
  onToggle: (id: number) => void;
  onToggleTodos: () => void;
}) {
  const pg = usePagination(trackers, 20);
  const todos = trackers.length > 0 && trackers.every((t) => selecionados.has(t.id));
  const algum = !todos && trackers.some((t) => selecionados.has(t.id));

  if (loading) return <TableSkeleton rows={8} cols={5} />;
  if (error) return <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os rastreadores" description="Veja o erro acima e tente novamente." />;
  if (trackers.length === 0) return <EmptyState title="Nenhum rastreador encontrado" description="Ajuste os filtros ou adicione um novo rastreador." />;

  return (
    <>
      <Table>
        <TableHead>
          {canEdit && (
            <Th className="w-10">
              <input
                type="checkbox"
                checked={todos}
                ref={(el) => { if (el) el.indeterminate = algum; }}
                onChange={onToggleTodos}
                aria-label={`Selecionar todos os ${trackers.length} rastreadores da lista`}
                title="Selecionar todos da lista (todas as páginas)"
                className="rounded border-slate-300"
              />
            </Th>
          )}
          <Th>IMEI / ID</Th>
          <Th>Equipamento</Th>
          <Th>Cliente / Veículo</Th>
          <Th>Status</Th>
          <Th className="w-36" />
        </TableHead>
        <TableBody>
          {pg.slice.map((tracker) => (
            <Tr key={tracker.id} selected={selecionados.has(tracker.id)}>
              {canEdit && (
                <Td>
                  <input
                    type="checkbox"
                    checked={selecionados.has(tracker.id)}
                    onChange={() => onToggle(tracker.id)}
                    aria-label={`Selecionar ${tracker.imei}`}
                    className="rounded border-slate-300"
                  />
                </Td>
              )}
              <Td className="font-mono text-sm">{tracker.imei}</Td>
              <Td>
                <p>{[tracker.brand, tracker.model].filter(Boolean).join(' ') || '—'}</p>
                {tracker.active_plan_name && <p className="text-xs text-brand-700 dark:text-brand-400">{tracker.active_plan_name}</p>}
              </Td>
              <Td>
                <p className="text-sm">{tracker.client_name ?? '—'}</p>
                <p className="text-xs text-slate-500">{tracker.vehicle_plate ? `Placa ${tracker.vehicle_plate}` : ''}</p>
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  <Badge variant={statusVariant(tracker.status)}>{statusLabel(tracker.status)}</Badge>
                  {tracker.integration_status && (
                    <Badge variant={integrationVariant(tracker.integration_status)}>{integrationLabel(tracker.integration_status)}</Badge>
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

function RastreadoresPageInner() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/rastreadores'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';
  const canEditContract = user?.role === 'admin';

  const [trackers, setTrackers] = useState<Tracker[]>([]);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [manufacturers, setManufacturers] = useState<ManufacturerOption[]>([]);
  const [plans, setPlans] = useState<PlanOption[]>([]);
  const [history, setHistory] = useState<TrackerHistory[]>([]);
  const [trackerContract, setTrackerContract] = useState<ContractInfo | null>(null);
  const [contractPlanId, setContractPlanId] = useState('');
  const [savingContractPlan, setSavingContractPlan] = useState(false);
  const [contractPlanFeedback, setContractPlanFeedback] = useState('');
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
  // Dia de vencimento a herdar (cliente > contratos do cliente/veículo). No SGR
  // o dia mora no contrato, então o cadastro do cliente quase sempre vem vazio.
  const [diaSugerido, setDiaSugerido] = useState<DiaSugerido | null>(null);
  const [buscandoDia, setBuscandoDia] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [manufacturerError, setManufacturerError] = useState('');

  // ── Cadastro em lote ──
  const [loteOpen, setLoteOpen] = useState(false);
  const [loteImeis, setLoteImeis] = useState('');
  const [loteForm, setLoteForm] = useState({ brand: '', model: '', status: 'em_estoque', carrier: '', notes: '' });
  const [loteResultado, setLoteResultado] = useState<LoteResultado | null>(null);
  const [loteBusy, setLoteBusy] = useState(false);
  const [loteError, setLoteError] = useState('');
  // Multiportal ligada no servidor (GET /integrations/multiportal/status): só
  // então o formulário oferece enviar logo após vincular à placa.
  const [multiportalAtiva, setMultiportalAtiva] = useState(false);
  const [enviarMultiportal, setEnviarMultiportal] = useState(true);

  async function loadBaseData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search) query.set('search', search);
      if (statusFilter) query.set('status', statusFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      if (vehicleFilter) query.set('vehicle_id', vehicleFilter);
      // Lista inteira do filtro: "selecionar todos" precisa ser todos mesmo.
      const [trackerResponse, clientResponse, vehicleResponse, planResponse] = await Promise.all([
        apiFetchAll<Tracker>(`/trackers?${query.toString()}`, currentToken, 500),
        apiFetchAll<ClientOption>('/clients', currentToken, 300),
        apiFetchAll<VehicleOption>('/vehicles', currentToken, 500),
        apiFetch<PlanOption[]>('/plans?limit=100', {}, currentToken).catch(() => [] as PlanOption[]),
      ]);
      setTrackers(trackerResponse);
      // A seleção acompanha a lista: o que saiu do filtro deixa de contar.
      const visiveis = new Set(trackerResponse.map((t) => t.id));
      setSelecionados((prev) => new Set([...prev].filter((id) => visiveis.has(id))));
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
      apiFetch<{ enabled: boolean }>('/integrations/multiportal/status', {}, currentToken)
        .then((status) => setMultiportalAtiva(!!status.enabled))
        .catch(() => setMultiportalAtiva(false));
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

  // Busca/filtros dinâmicos (sem precisar clicar em "Filtrar")
  const searchDebounced = useDebouncedValue(search);
  useEffectSkipFirst(() => {
    if (token) loadBaseData(token);
  }, [searchDebounced, statusFilter, clientFilter, vehicleFilter]);

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
      apiFetchList<Tracker>(`/trackers?vehicle_id=${selectedTracker.vehicle_id}&limit=20`, {}, token)
        .then((items) => setVehicleTrackers(items.filter((t) => t.id !== selectedTracker.id)))
        .catch(() => setVehicleTrackers([]));
    } else {
      setVehicleTrackers([]);
    }
  }, [token, selectedTracker?.id, modalOpen]);

  // Deep-link da Busca Global (Ctrl+K): "?focus=<id>" abre o rastreador
  // direto — busca pelo id (não pela lista carregada, que é limitada/
  // filtrada) e abre o mesmo modal do botão de detalhes. "?assistantAction="
  // é o mesmo mecanismo usado pelo Assistente de Ações para "Novo
  // rastreador" (com veículo pré-preenchido) e "Consultar rastreador".
  const searchParams = useSearchParams();
  const router = useRouter();
  useEffect(() => {
    if (!token) return;
    const focusId = searchParams.get('focus');
    const assistantAction = searchParams.get('assistantAction');

    if (focusId) {
      router.replace('/rastreadores');
      apiFetch<Tracker>(`/trackers/${focusId}`, {}, token)
        .then((tracker) => {
          setSelectedTracker(tracker);
          setDetailsTab('dados');
          setDetailsOpen(true);
        })
        .catch((err) => setError(parseError(err)));
      return;
    }

    if (assistantAction === 'new') {
      const prefillVehicleId = searchParams.get('prefillVehicleId');
      const prefillClientId = searchParams.get('prefillClientId');
      router.replace('/rastreadores');
      openCreateModal({
        vehicleId: prefillVehicleId ? Number(prefillVehicleId) : undefined,
        clientId: prefillClientId ? Number(prefillClientId) : undefined,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, searchParams]);

  const [multiportalSync, setMultiportalSync] = useState<{ loading: boolean; result: TrackerLinkFlow | null; error: string }>({ loading: false, result: null, error: '' });

  // Mesmo "Sync completo" da tela de Integração (cliente → veículo →
  // equipamento → vínculo). O backend grava o resultado em
  // integration_status; aqui o badge e a lista acompanham sem recarregar.
  async function sincronizarMultiportal(tracker: Tracker) {
    if (!token) return;
    setMultiportalSync({ loading: true, result: null, error: '' });
    try {
      const flow = await apiFetch<TrackerLinkFlow>(`/integrations/multiportal/trackers/${tracker.id}/sync-flow`, { method: 'POST' }, token);
      setMultiportalSync({ loading: false, result: flow, error: '' });
      const integrationStatus = flow.overall_success ? 'sincronizado' : 'erro';
      setSelectedTracker((atual) => (atual && atual.id === tracker.id ? { ...atual, integration_status: integrationStatus } : atual));
      setTrackers((items) => items.map((item) => (item.id === tracker.id ? { ...item, integration_status: integrationStatus } : item)));
    } catch (err) {
      setMultiportalSync({ loading: false, result: null, error: parseError(err) });
    }
  }

  // Ações contextuais do Assistente de Ações (Ctrl+K) enquanto um
  // rastreador está aberto no modal de detalhes.
  useAssistantContextActions(
    detailsOpen && selectedTracker
      ? [
          ...(canEdit && selectedTracker.vehicle_id
            ? [{
                id: 'sincronizar-multiportal-contexto',
                label: 'Sincronizar com a Multiportal',
                run: () => sincronizarMultiportal(selectedTracker),
              }]
            : []),
          ...(canEdit
            ? [{
                id: 'excluir-rastreador-contexto',
                label: 'Excluir rastreador',
                run: () => { setDetailsOpen(false); deleteTracker(); },
                manualFeedback: true,
              }]
            : []),
        ]
      : [],
  );

  const filteredVehicles = useMemo(() => {
    if (!form.client_id) return vehicles;
    return vehicles.filter((item) => item.client_id === Number(form.client_id));
  }, [vehicles, form.client_id]);

  const contratoAtivoNoVeiculo = Boolean(
    isEditing && selectedTracker?.active_plan_name
    && selectedTracker.vehicle_id === Number(form.vehicle_id),
  );

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

  useEffect(() => {
    setDiaSugerido(null);
    if (!modalOpen || !token || !form.vehicle_id) return;
    let ativo = true;
    setBuscandoDia(true);
    apiFetch<DiaSugerido>(`/trackers/billing-day-suggestion?vehicle_id=${form.vehicle_id}`, {}, token)
      .then((sugestao) => {
        if (!ativo) return;
        setDiaSugerido(sugestao);
        if (sugestao.dia) {
          // Não sobrescreve um dia já digitado.
          setForm((prev) => (prev.link_billing_day ? prev : { ...prev, link_billing_day: String(sugestao.dia) }));
        }
      })
      .catch(() => { if (ativo) setDiaSugerido(null); })
      .finally(() => { if (ativo) setBuscandoDia(false); });
    return () => { ativo = false; };
  }, [modalOpen, token, form.vehicle_id]);

  function resetForm() {
    setForm(initialForm);
    setIsEditing(false);
    setEnviarMultiportal(true);
  }

  /* ── Cadastro em lote ────────────────────────────────────────────────── */

  /** Aceita a lista colada em qualquer formato: uma por linha, vírgula, ponto
   *  e vírgula, tabulação ou espaço (é comum vir de planilha). */
  const imeisDaCaixa = () =>
    loteImeis.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);

  function abrirLote() {
    setLoteImeis('');
    setLoteForm({ brand: '', model: '', status: 'em_estoque', carrier: '', notes: '' });
    setLoteResultado(null);
    setLoteError('');
    setLoteOpen(true);
  }

  async function enviarLote(simular: boolean) {
    const imeis = imeisDaCaixa();
    if (imeis.length === 0) { setLoteError('Cole ao menos um IMEI.'); return; }
    if (!loteForm.brand.trim() || !loteForm.model.trim()) {
      setLoteError('Informe a marca e o modelo — eles valem para todos do lote.');
      return;
    }
    setLoteError(''); setLoteBusy(true);
    try {
      const res = await apiFetch<LoteResultado>('/trackers/lote', {
        method: 'POST',
        body: JSON.stringify({
          imeis,
          brand: loteForm.brand.trim(),
          model: loteForm.model.trim(),
          status: loteForm.status,
          carrier: loteForm.carrier.trim() || null,
          notes: loteForm.notes.trim() || null,
          simular,
        }),
      }, token);
      setLoteResultado(res);
      if (!simular) {
        setFeedback(`${res.criados} rastreador(es) cadastrado(s).${res.ignorados ? ` ${res.ignorados} ignorado(s).` : ''}`);
        if (token) loadBaseData(token);
      }
    } catch (err) { setLoteError(parseError(err)); } finally { setLoteBusy(false); }
  }

  function openCreateModal(prefill?: { vehicleId?: number; clientId?: number }) {
    resetForm();
    if (prefill?.vehicleId) {
      const vehicle = vehicles.find((v) => v.id === prefill.vehicleId);
      setForm((prev) => ({
        ...prev,
        vehicle_id: String(prefill.vehicleId),
        client_id: vehicle ? String(vehicle.client_id) : prev.client_id,
      }));
    } else if (prefill?.clientId) {
      setForm((prev) => ({ ...prev, client_id: String(prefill.clientId) }));
    }
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(tracker: Tracker) {
    setSelectedTracker(tracker);
    setTrackerContract(null);
    setContractPlanId(tracker.active_plan_id ? String(tracker.active_plan_id) : '');
    setContractPlanFeedback('');
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
      // Contrato criado para um rastreador já instalado começa, por padrão, na
      // data da instalação (não no dia de hoje).
      link_start_date: tracker.install_date || new Date().toISOString().split('T')[0],
      // Preenchido pela sugestão do backend (cliente > contratos), ver efeito.
      link_billing_day: '',
      link_payment_method: '',
    });
    setIsEditing(true);
    setModalOpen(true);
  }

  async function saveContractPlan() {
    if (!token || !canEditContract || !selectedTracker || !trackerContract || !contractPlanId) return;
    if (trackerContract.tracker_id !== selectedTracker.id || trackerContract.vehicle_id !== selectedTracker.vehicle_id) return;
    setSavingContractPlan(true);
    setModalError('');
    setContractPlanFeedback('');
    try {
      const updatedContract = await apiFetch<ContractInfo>(`/contracts/${trackerContract.id}`, {
        method: 'PUT',
        body: JSON.stringify({ plan_id: Number(contractPlanId) }),
      }, token);
      const updatedTracker: Tracker = {
        ...selectedTracker,
        active_plan_id: updatedContract.plan_id,
        active_plan_name: updatedContract.plan_name,
      };
      setTrackerContract(updatedContract);
      setSelectedTracker(updatedTracker);
      setTrackers((items) => items.map((item) => item.id === updatedTracker.id ? updatedTracker : item));
      setContractPlanFeedback('Plano do contrato atualizado. Confira as cobranças já geradas no Financeiro.');
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSavingContractPlan(false);
    }
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
      const isTransfer = Boolean(
        isEditing && selectedTracker?.vehicle_id && form.vehicle_id
        && selectedTracker.vehicle_id !== Number(form.vehicle_id),
      );
      // Rastreador já instalado neste veículo e um plano escolhido: antes o PUT
      // salvava só os dados técnicos e o plano era descartado sem aviso.
      const isAddingContract = Boolean(
        isEditing && selectedTracker?.vehicle_id && form.link_plan_id
        && selectedTracker.vehicle_id === Number(form.vehicle_id),
      );
      const isRemovingVehicle = Boolean(isEditing && selectedTracker?.vehicle_id && !form.vehicle_id);
      if (isRemovingVehicle) {
        throw new Error('Use a desinstalação do veículo para remover um vínculo existente.');
      }
      if (form.vehicle_id && form.link_plan_id && !form.link_billing_day) {
        throw new Error('Informe o dia do vencimento do contrato.');
      }
      if (isTransfer && !window.confirm(
        'Este rastreador já está instalado em outro veículo. A transferência encerrará o contrato anterior, '
        + 'desfará o vínculo no Multiportal e criará o novo vínculo. Deseja continuar?',
      )) {
        return;
      }

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
      // Transferência já passa pela Multiportal no próprio vínculo
      // (multiportal_synchronized); aí não há o que reenviar.
      let multiportalJaSincronizado = false;
      if (isEditing && selectedTracker) {
        // O PUT genérico não pode alterar vínculos. Em uma vinculação/transferência,
        // salva somente dados técnicos e delega toda a relação ao endpoint seguro.
        const updatePayload: Record<string, unknown> = { ...payload };
        if (isLinkingVehicle) {
          delete updatePayload.vehicle_id;
          delete updatePayload.client_id;
        }
        saved = await apiFetch<Tracker>(`/trackers/${selectedTracker.id}`, { method: 'PUT', body: JSON.stringify(updatePayload) }, token);

        if (isLinkingVehicle || isAddingContract) {
          const linkResult = await apiFetch<{ tracker: Tracker; multiportal_synchronized?: boolean }>(`/trackers/${selectedTracker.id}/link-vehicle`, {
            method: 'POST',
            body: JSON.stringify({
              vehicle_id: Number(form.vehicle_id),
              confirm_transfer: isTransfer,
              plan_id: form.link_plan_id ? Number(form.link_plan_id) : null,
              start_date: form.link_start_date,
              billing_day: form.link_billing_day ? Number(form.link_billing_day) : null,
              payment_method: form.link_payment_method || null,
            }),
          }, token);
          saved = linkResult.tracker;
          multiportalJaSincronizado = !!linkResult.multiportal_synchronized;
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
            }),
          }, token);
        }
      }

      // Enviar à Multiportal logo após vincular à placa: o mesmo sync completo
      // do botão "Sincronizar" dos detalhes. O vínculo já está salvo — falha
      // aqui vira aviso, não desfaz nada.
      const vinculouPlaca = isEditing ? isLinkingVehicle : !!form.vehicle_id;
      let avisoMultiportal = '';
      let erroMultiportal = '';
      if (vinculouPlaca && enviarMultiportal && multiportalAtiva && !multiportalJaSincronizado) {
        try {
          const flow = await apiFetch<TrackerLinkFlow>(`/integrations/multiportal/trackers/${saved.id}/sync-flow`, { method: 'POST' }, token);
          saved = { ...saved, integration_status: flow.overall_success ? 'sincronizado' : 'erro' };
          if (flow.overall_success) {
            avisoMultiportal = ' Enviado para a Multiportal.';
          } else {
            erroMultiportal = 'Rastreador salvo, mas o envio para a Multiportal terminou com erro. Veja as etapas em Detalhes → Sincronizar.';
          }
        } catch (err) {
          erroMultiportal = `Rastreador salvo, mas não foi possível enviar para a Multiportal agora: ${parseError(err)} Tente em Detalhes → Sincronizar.`;
        }
      }

      setFeedback(
        (isEditing && isAddingContract ? 'Rastreador atualizado e contrato criado.'
          : isEditing ? 'Rastreador atualizado com sucesso.' : 'Rastreador cadastrado com sucesso.')
        + avisoMultiportal,
      );
      setModalOpen(false);
      resetForm();
      await loadBaseData(token);
      setSelectedTracker(saved);
      // Depois do loadBaseData, que limpa o erro da página ao recarregar.
      if (erroMultiportal) setError(erroMultiportal);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  function alternarSelecao(id: number) {
    setSelecionados((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function alternarTodos() {
    setSelecionados((prev) => (
      trackers.length > 0 && trackers.every((t) => prev.has(t.id)) ? new Set() : new Set(trackers.map((t) => t.id))
    ));
  }

  function executarLote(acao: AcaoLote, corpo: AcaoLoteCorpo) {
    return apiFetch<AcaoLoteResultado>(`/trackers/lote/${acao}`, { method: 'POST', body: JSON.stringify(corpo) }, token);
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
          {(guardError || error) ? <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section>
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Controle de rastreadores"
            actions={
              <div className="flex items-center gap-2">
                {token && <ExportButton path="exports/trackers" basename="rastreadores" token={token} params={{ status: statusFilter, client_id: clientFilter }} />}
                {canEdit && <Button variant="secondary" onClick={abrirLote}>Cadastrar em lote</Button>}
                {canEdit && <Button onClick={() => openCreateModal()}>Adicionar rastreador</Button>}
              </div>
            }
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <input className={fieldClass} style={{ maxWidth: 280 }} placeholder="IMEI, modelo ou placa" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className={fieldClass} style={{ width: 180 }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Todos os status</option>
              {statusOptions.map((o) => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
            </select>
            <div style={{ width: 260 }}>
              <ClientAutocomplete
                clients={clients}
                value={clientFilter}
                onChange={setClientFilter}
                placeholder="Todos os clientes"
              />
            </div>
            <Button variant="secondary" onClick={() => token && loadBaseData(token)} disabled={loading}>
              {loading ? 'Atualizando…' : 'Atualizar'}
            </Button>
          </div>
          <div className="mt-4">
            {canEdit && (
              <AcoesEmLote
                ids={[...selecionados]}
                onLimpar={() => setSelecionados(new Set())}
                onExecutar={executarLote}
                onConcluido={(mensagem) => {
                  setError('');
                  setFeedback(mensagem);
                  setSelecionados(new Set());
                  if (token) loadBaseData(token);
                }}
              />
            )}
            <RastreadoresTableContent
              trackers={trackers}
              loading={loading}
              error={error}
              canEdit={canEdit}
              selecionados={selecionados}
              onToggle={alternarSelecao}
              onToggleTodos={alternarTodos}
              onDetails={(t) => { setSelectedTracker(t); setDetailsTab('dados'); setDetailsOpen(true); setMultiportalSync({ loading: false, result: null, error: '' }); }}
              onEdit={openEditModal}
            />
          </div>
        </Card>
      </section>

      {/* Indicadores abaixo do cadastro (padrão de todas as telas) */}
      <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Rastreadores cadastrados" value={stats.total} hint="Base técnica disponível" icon={<Radio className="h-5 w-5" />} />
        <StatCard label="Instalados" value={stats.installed} hint="Equipamentos em produção" tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
        <StatCard label="Em estoque" value={stats.stock} hint="Prontos para reutilização" tone="brand" icon={<Package className="h-5 w-5" />} />
        <StatCard label="Em manutenção" value={stats.maintenance} hint="Exigem acompanhamento" tone="warning" icon={<Wrench className="h-5 w-5" />} />
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
            <Button
              variant="danger"
              onClick={() => { setDetailsOpen(false); deleteTracker(); }}
              className="text-xs"
              disabled={!!selectedTracker.vehicle_id || !['extraviado', 'em_manutencao'].includes(selectedTracker.status)}
              title="Só é possível excluir rastreador extraviado ou em manutenção, sem veículo"
            >
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
                  ['Integração', <Badge key="i" variant={integrationVariant(selectedTracker.integration_status)}>{integrationLabel(selectedTracker.integration_status)}</Badge>],
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
                    <p className="text-2xs font-semibold uppercase tracking-widest text-slate-500">{label}</p>
                    <div className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</div>
                  </div>
                ))}
                <div className="col-span-2 rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-2xs font-semibold uppercase tracking-widest text-slate-500">Envio para a Multiportal</p>
                    <div className="flex flex-wrap justify-end gap-2">
                      {canEdit && (
                        <Button
                          type="button"
                          className="px-3 py-1.5 text-xs"
                          disabled={multiportalSync.loading || !selectedTracker.vehicle_id}
                          title={selectedTracker.vehicle_id
                            ? 'Envia cliente, veículo e equipamento para a Multiportal e refaz o vínculo'
                            : 'Vincule o rastreador a um veículo para sincronizar'}
                          onClick={() => sincronizarMultiportal(selectedTracker)}
                        >
                          {multiportalSync.loading ? 'Sincronizando…' : 'Sincronizar'}
                        </Button>
                      )}
                    </div>
                  </div>
                  {multiportalSync.error && (
                    <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{multiportalSync.error}</p>
                  )}
                  {multiportalSync.result && (
                    <div className="mt-2 space-y-1.5">
                      <Badge variant={multiportalSync.result.overall_success ? 'success' : 'danger'}>
                        {multiportalSync.result.overall_success ? 'Sincronização concluída' : 'Sincronização com erros'}
                      </Badge>
                      {multiportalSync.result.steps.map((step, i) => (
                        <p key={i} className="text-xs text-slate-600 dark:text-slate-300">
                          {step.friendly_title ? `${step.friendly_title}: ` : ''}{step.friendly_message ?? (step.success ? 'OK' : 'Falhou')}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
                {selectedTracker.notes && (
                  <div className="col-span-2 rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-2xs font-semibold uppercase tracking-widest text-slate-500">Observações</p>
                    <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{selectedTracker.notes}</p>
                  </div>
                )}
                {vehicleTrackers.length > 0 && (
                  <div className="col-span-2 space-y-1.5">
                    <p className="text-2xs font-semibold uppercase tracking-widest text-slate-500">Outros rastreadores neste veículo</p>
                    {vehicleTrackers.map((t) => (
                      <div key={t.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/50">
                        <span className="font-mono text-sm">{t.imei}</span>
                        <span className="text-xs text-slate-500">{t.active_plan_name ?? 'sem plano'}</span>
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
                          <time className="mt-0.5 block text-3xs text-slate-500">{entry.created_at ? new Date(entry.created_at).toLocaleString('pt-BR') : entry.event_date ?? '—'}</time>
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
                      {trackerContract.start_date && <div><span className="font-medium">Início:</span> {formatDate(trackerContract.start_date)}</div>}
                      {trackerContract.next_due_date && <div><span className="font-medium">Próx. venc.:</span> {formatDate(trackerContract.next_due_date)}</div>}
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

      {/* ── Cadastro em lote ── */}
      <Modal
        open={loteOpen}
        onClose={() => setLoteOpen(false)}
        title="Cadastrar rastreadores em lote"
        description="Cole os IMEIs recebidos na remessa. Marca, modelo e demais campos valem para todos."
        size="xl"
      >
        <div className="space-y-5">
          {loteError && (
            <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{loteError}</p>
          )}

          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
              IMEIs / números de série <span className="text-rose-500">*</span>
            </label>
            <textarea
              className={fieldClass}
              rows={7}
              placeholder={'Um por linha — ou separados por vírgula.\n\n869671075760726\n869731058404187\n907076841'}
              value={loteImeis}
              onChange={(e) => { setLoteImeis(e.target.value); setLoteResultado(null); }}
            />
            <p className="mt-1 text-xs text-slate-500">
              {imeisDaCaixa().length} número(s) na lista · aceita quebra de linha, vírgula, ponto e vírgula ou tabulação (colar da planilha funciona)
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                Marca <span className="text-rose-500">*</span>
              </label>
              <input className={fieldClass} value={loteForm.brand} placeholder="Ex.: Suntech"
                     onChange={(e) => setLoteForm((p) => ({ ...p, brand: e.target.value }))} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                Modelo <span className="text-rose-500">*</span>
              </label>
              <input className={fieldClass} value={loteForm.model} placeholder="Ex.: ST310U"
                     onChange={(e) => setLoteForm((p) => ({ ...p, model: e.target.value }))} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Situação</label>
              <select className={fieldClass} value={loteForm.status}
                      onChange={(e) => setLoteForm((p) => ({ ...p, status: e.target.value }))}>
                {statusOptions.map((o) => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Operadora do chip</label>
              <input className={fieldClass} value={loteForm.carrier} placeholder="Ex.: Vivo"
                     onChange={(e) => setLoteForm((p) => ({ ...p, carrier: e.target.value }))} />
            </div>
            <div className="md:col-span-2">
              <label className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">Observações</label>
              <input className={fieldClass} value={loteForm.notes} placeholder="Ex.: Nota fiscal 1234 — remessa de 29/07"
                     onChange={(e) => setLoteForm((p) => ({ ...p, notes: e.target.value }))} />
            </div>
          </div>

          {/* Conferência antes de gravar */}
          {loteResultado && (
            <div className="rounded-2xl border border-slate-200 dark:border-slate-700">
              <div className="flex flex-wrap items-center gap-4 border-b border-slate-100 px-4 py-3 text-sm dark:border-slate-800">
                <span className="font-semibold text-slate-700 dark:text-slate-200">
                  {loteResultado.simulacao ? 'Conferência' : 'Resultado'}
                </span>
                <span className="text-emerald-600 dark:text-emerald-400">
                  {loteResultado.criados} {loteResultado.simulacao ? 'a cadastrar' : 'cadastrado(s)'}
                </span>
                {loteResultado.ignorados > 0 && (
                  <span className="text-amber-600 dark:text-amber-400">{loteResultado.ignorados} ignorado(s)</span>
                )}
                <span className="text-slate-500">de {loteResultado.total_enviados} enviado(s)</span>
              </div>
              <div className="max-h-56 overflow-y-auto">
                {loteResultado.itens.filter((i) => i.situacao !== 'criado').length === 0 ? (
                  <p className="px-4 py-3 text-sm text-slate-500">Todos os números estão válidos e disponíveis.</p>
                ) : (
                  <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
                    {loteResultado.itens.filter((i) => i.situacao !== 'criado').map((i, idx) => (
                      <li key={`${i.imei}-${idx}`} className="flex items-center justify-between gap-3 px-4 py-2">
                        <span className="font-mono text-slate-700 dark:text-slate-200">{i.imei || '—'}</span>
                        <span className="text-xs text-slate-500">
                          {i.situacao === 'ja_existe' && 'Já cadastrado'}
                          {i.situacao === 'repetido_no_lote' && 'Repetido na lista'}
                          {i.situacao === 'invalido' && (i.motivo || 'Inválido')}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-3">
            <Button variant="ghost" onClick={() => setLoteOpen(false)}>Fechar</Button>
            <Button variant="secondary" onClick={() => enviarLote(true)} disabled={loteBusy}>
              {loteBusy ? 'Conferindo…' : 'Conferir'}
            </Button>
            <Button onClick={() => enviarLote(false)} disabled={loteBusy || imeisDaCaixa().length === 0}>
              {loteBusy ? 'Cadastrando…' : `Cadastrar ${imeisDaCaixa().length || ''}`}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar rastreador' : 'Novo rastreador'} description="Cadastre o equipamento em um fluxo mais limpo, com foco no identificador técnico, vínculo e dados essenciais." size="xl">
        <form className="space-y-6" onSubmit={submitTracker}>
          {modalError && <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Número de série / ID" value={form.imei} onChange={(e) => setForm((prev) => ({ ...prev, imei: onlyDigits(e.target.value).slice(0, 20) }))} required />
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as TrackerStatus }))}>{statusOptions.map((option) => <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>)}</select>
            <input className={fieldClass} placeholder="Marca" value={form.brand} onChange={(e) => setForm((prev) => ({ ...prev, brand: e.target.value.slice(0, 60) }))} required />
            <input className={fieldClass} placeholder="Modelo" value={form.model} onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value.slice(0, 60) }))} required />
          </div>

          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Vínculo do cliente</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Busque pelo nome (a lista aparece enquanto digita) ou por CPF/CNPJ.</p>
            <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto]">
              <input className={fieldClass} placeholder="CPF/CNPJ do cliente" value={form.client_lookup_document} onChange={(e) => setForm((prev) => ({ ...prev, client_lookup_document: formatCpfCnpj(e.target.value) }))} />
              <Button type="button" onClick={findClientByDocument}>Buscar cliente</Button>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <ClientAutocomplete
                clients={clients}
                value={form.client_id}
                onChange={(id) => setForm((prev) => ({ ...prev, client_id: id, vehicle_id: '', link_plan_id: '' }))}
                placeholder="Buscar cliente por nome…"
              />
              <select className={fieldClass} value={form.vehicle_id} onChange={(e) => {
                const vid = e.target.value;
                setForm((prev) => ({ ...prev, vehicle_id: vid, link_billing_day: '' }));
              }}><option value="">Sem veículo</option>{filteredVehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.plate} {vehicle.model ? `• ${vehicle.model}` : ''}</option>)}</select>
            </div>
          </div>

          {form.vehicle_id && (
            <div className="rounded-[24px] border border-brand-200 bg-brand-50/50 p-5 dark:border-cyan-900 dark:bg-cyan-950/30">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">Plano contratado</p>
              {contratoAtivoNoVeiculo ? (
                <div className="mt-2 space-y-3 text-sm text-slate-600 dark:text-slate-300">
                  <p>Contrato ativo neste veículo: <span className="font-semibold">{selectedTracker?.active_plan_name}</span>.</p>
                  {canEditContract ? (
                    <>
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                        <label className="min-w-0 flex-1">
                          <span className="mb-1 block text-xs font-semibold">Alterar plano do contrato</span>
                          <select className={fieldClass} value={contractPlanId} onChange={(e) => { setContractPlanId(e.target.value); setContractPlanFeedback(''); }}>
                            {plans.filter((plan) => plan.active !== false || plan.id === selectedTracker?.active_plan_id).map((plan) => (
                              <option key={plan.id} value={plan.id}>{plan.name} — R$ {Number(plan.price ?? 0).toFixed(2)}{pricePeriodSuffix(plan.billing_interval_months)}</option>
                            ))}
                          </select>
                        </label>
                        <Button type="button" onClick={saveContractPlan} disabled={savingContractPlan || !trackerContract || trackerContract.tracker_id !== selectedTracker?.id || trackerContract.vehicle_id !== selectedTracker?.vehicle_id || !contractPlanId || Number(contractPlanId) === trackerContract.plan_id}>
                          {savingContractPlan ? 'Salvando...' : 'Salvar plano'}
                        </Button>
                      </div>
                      <p className="text-xs text-amber-700 dark:text-amber-300">
                        A mudança vale para novas cobranças. {trackerContract?.open_billings ? `${trackerContract.open_billings} cobrança(s) em aberto mantêm o valor anterior; confira-as no Financeiro.` : 'Cobranças já geradas não são recalculadas.'}
                      </p>
                      {contractPlanFeedback && <p className="text-xs text-emerald-700 dark:text-emerald-300">{contractPlanFeedback}</p>}
                    </>
                  ) : <p>Peça a um administrador para alterar o plano deste contrato.</p>}
                </div>
              ) : (
              <>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                {isEditing && selectedTracker?.vehicle_id === Number(form.vehicle_id)
                  ? 'Rastreador já instalado neste veículo, sem contrato. Selecione o plano para criar o contrato ao salvar.'
                  : 'Selecione o plano para criar o contrato automaticamente ao vincular. Deixe em branco para vincular sem contrato.'}
              </p>
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
                  {plans.map((p) => <option key={p.id} value={p.id}>{p.name} — R$ {Number(p.price ?? 0).toFixed(2)}{pricePeriodSuffix(p.billing_interval_months)}</option>)}
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
                      <option value="dinheiro">Dinheiro</option>
                    </select>
                    {/* Dia de vencimento: herdado do cliente */}
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
                      <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Dia do vencimento</p>
                      {/* O input fica SEMPRE montado: antes ele era renderizado
                          só quando o campo estava vazio, então sumia no primeiro
                          dígito e "10" travava em "1". */}
                      <div className="mt-1 flex items-center justify-between gap-3">
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                          {form.link_billing_day
                            ? <>Todo dia <span className="font-bold text-brand-700 dark:text-brand-300">{form.link_billing_day}</span> · {diaSugerido?.dia === Number(form.link_billing_day) ? origemDia(diaSugerido) : 'informado manualmente'}</>
                            : buscandoDia
                              ? 'Buscando o dia do cliente…'
                              : <span className="text-amber-600 dark:text-amber-400">Cliente sem dia de vencimento no cadastro nem em contratos — informe ao lado</span>}
                        </p>
                        <BillingDayInput
                          value={form.link_billing_day}
                          onChange={(v) => setForm((prev) => ({ ...prev, link_billing_day: v }))}
                          className="w-20 shrink-0 text-center"
                        />
                      </div>
                      {diaSugerido && diaSugerido.alternativas.length > 0 && (
                        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                          O cliente tem contratos com outros dias ({diaSugerido.alternativas.join(', ')}). Confira antes de salvar.
                        </p>
                      )}
                      {erroDiaVencimento(form.link_billing_day) && (
                        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                          {erroDiaVencimento(form.link_billing_day)}
                        </p>
                      )}
                    </div>
                  </>
                )}
              </div>
              </>
              )}
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
                  <button type="button" onClick={() => token && loadBaseData(token)} className="text-xs font-semibold text-brand-700 underline dark:text-cyan-400">Recarregar</button>
                </div>
              )}
            </div>
            <input className={fieldClass} placeholder="Linha / MSISDN" value={form.sim_number} onChange={(e) => setForm((prev) => ({ ...prev, sim_number: onlyDigits(e.target.value).slice(0, 20) }))} />
            <input className={fieldClass} placeholder="ICCID" value={form.sim_iccid} onChange={(e) => setForm((prev) => ({ ...prev, sim_iccid: onlyDigits(e.target.value).slice(0, 22) }))} />
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Data de instalação</span>
              <input type="date" className={fieldClass} value={form.install_date} onChange={(e) => setForm((prev) => ({ ...prev, install_date: e.target.value }))} />
            </label>
            <textarea className={`${areaClass} md:col-span-3`} placeholder="Observações técnicas" value={form.notes} onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value.slice(0, 500) }))} />
          </div>

          {canEdit && multiportalAtiva && !!form.vehicle_id
            && (!isEditing || selectedTracker?.vehicle_id !== Number(form.vehicle_id)) && (
            <label className="flex items-start gap-3 rounded-2xl border border-brand-200 bg-brand-50/60 px-4 py-3 dark:border-brand-900/40 dark:bg-brand-950/30">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-brand-600"
                checked={enviarMultiportal}
                onChange={(e) => setEnviarMultiportal(e.target.checked)}
              />
              <span>
                <span className="block text-sm font-semibold text-slate-900 dark:text-white">Enviar para a Multiportal logo após vincular</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">
                  Envia cliente, veículo e equipamento e cria o vínculo na hora — o mesmo do botão Sincronizar nos detalhes.
                  Desmarcado, envie depois em Detalhes → Sincronizar.
                </span>
              </span>
            </label>
          )}

          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar rastreador' : 'Cadastrar rastreador'}</Button>
          </div>
        </form>
      </Modal>
    </PageShell>
  );
}

// useSearchParams (deep-link "?focus=" da Busca Global) exige um limite de
// Suspense — senão o build estático do Next falha com "should be wrapped in
// a suspense boundary" ao pré-renderizar a rota.
export default function RastreadoresPage() {
  return (
    <Suspense fallback={null}>
      <RastreadoresPageInner />
    </Suspense>
  );
}
