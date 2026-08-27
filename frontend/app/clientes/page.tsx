'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Users, AlertTriangle, Building2, CheckCircle2, Plus, Trash2, Car, Coins, DollarSign, PawPrint, Pencil, Printer, Search } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useDebouncedValue } from '@/lib/use-debounced-value';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { usePagination, Pagination } from '@/components/ui/pagination';
import { ExportButton } from '@/components/ui/export-button';
import { apiFetch, API_URL } from '@/lib/api';
import { entregarArquivo, nomeArquivoCliente } from '@/lib/arquivo';
import { enviarBoletoEmail, enviarBoletoWhats } from '@/lib/boleto-mensagem';
import { fetchAddressByCep } from '@/lib/cep';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import type { DocumentReviewStatus as ReviewStatus } from '@/lib/domain-types';

import type {
  Client,
  ClientDocument,
  ContactItem,
  VehicleSummary,
  BillingItem,
  CarneItem,
  IntervContract,
  BillingChange,
  NfseItem,
  TimelineContract,
  TimelineOrder,
  TimelineBilling,
  TimelineEvent,
  ClientFormState,
  ContractSheetItem,
  ClientSortField,
  ClientSort,
} from './_components/types';
import {
  formatCurrency,
  orderTypeLabel,
  emptyContact,
  initialForm,
  parseError,
  normalizeEmail,
  parseExtraEmails,
  valorComJuros,
} from './_components/helpers';
import { ActionBtn } from './_components/action-btn';
import { SortTh } from './_components/sort-th';
import { UnifyBillingModal } from './_components/unify-billing-modal';
import { EditBillingModal } from './_components/edit-billing-modal';
import { BillingHistoryModal } from './_components/billing-history-modal';
import { BillingsModal } from './_components/billings-modal';
import { ClientDetailModal } from './_components/client-detail-modal';
import { ClientFormModal } from './_components/client-form-modal';
import { VehiclesModal } from './_components/vehicles-modal';
import { IntervenienteModal } from './_components/interveniente-modal';
import { NfseModal } from './_components/nfse-modal';
import { ContractSheetModal } from './_components/contract-sheet-modal';
import {
  clientsKeys,
  useClientVehiclesDetailedQuery,
  useClientsQuery,
  useVehicleSummariesQuery,
  vehicleSummariesKeys,
} from './_components/queries';

export default function ClientesPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/clientes'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';
  // Ações financeiras (boletos, interveniente, NFS-e, ficha) usam endpoints
  // restritos a admin/financeiro — esconder do operacional evita 403 no clique
  const canFinance = !!user && (user.role === 'admin' || user.role === 'financeiro');

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
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [docCategory, setDocCategory] = useState('cnh');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [clientTimeline, setClientTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);

  // Ordenação + paginação da tabela (padrão do sistema de referência)
  const [clientSort, setClientSort] = useState<ClientSort>({ field: 'name', dir: 'asc' });
  const [pageSize, setPageSize] = useState(10);

  function toggleClientSort(field: ClientSortField) {
    setClientSort((prev) =>
      prev.field === field ? { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { field, dir: 'asc' },
    );
  }

  const queryClient = useQueryClient();
  // loadClients recarregava clientes + veículos juntos (1 Promise.all); mantém
  // o mesmo par aqui para quem invalida após criar/editar/excluir cliente.
  function invalidateClients() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: clientsKeys.all }),
      queryClient.invalidateQueries({ queryKey: vehicleSummariesKeys.all }),
    ]);
  }

  // Busca/filtros dinâmicos: a query recarrega sozinha quando a chave muda —
  // debounce só na busca, para não disparar 1 requisição por tecla digitada.
  const searchDebounced = useDebouncedValue(search);
  const clientsQuery = useClientsQuery(token, { search: searchDebounced, status: statusFilter, type: typeFilter });
  const vehicleSummariesQuery = useVehicleSummariesQuery(token);
  const clients = useMemo(() => clientsQuery.data ?? [], [clientsQuery.data]);
  const vehicleSummaries = useMemo(() => vehicleSummariesQuery.data ?? [], [vehicleSummariesQuery.data]);
  const loading = clientsQuery.isFetching || vehicleSummariesQuery.isFetching;
  const listError = clientsQuery.isError ? parseError(clientsQuery.error) : '';

  // Mantém o cliente aberto no drawer sincronizado após um refetch da lista
  // (ex.: outra aba editou o mesmo cliente) — mesma lógica que loadClients
  // fazia manualmente antes de virar useQuery.
  useEffect(() => {
    if (selectedClient && clientsQuery.data) {
      const refreshed = clientsQuery.data.find((item) => item.id === selectedClient.id) || null;
      setSelectedClient(refreshed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientsQuery.data]);

  const sortedClients = useMemo(() => {
    const { field, dir } = clientSort;
    const factor = dir === 'asc' ? 1 : -1;
    return [...clients].sort((a, b) => {
      if (field === 'id') return (a.id - b.id) * factor;
      const av = String((a as unknown as Record<string, unknown>)[field] ?? '').toLowerCase();
      const bv = String((b as unknown as Record<string, unknown>)[field] ?? '').toLowerCase();
      return av.localeCompare(bv, 'pt-BR') * factor;
    });
  }, [clients, clientSort]);

  const pg = usePagination(sortedClients, pageSize);

  // Modal "Veículos vinculados ao cliente"
  const [vehiclesModalOpen, setVehiclesModalOpen] = useState(false);
  const [vehiclesModalClient, setVehiclesModalClient] = useState<Client | null>(null);

  // Modal "Boletos do cliente"
  const [billingsModalOpen, setBillingsModalOpen] = useState(false);
  const [billingsModalClient, setBillingsModalClient] = useState<Client | null>(null);
  const [clientBillings, setClientBillings] = useState<BillingItem[]>([]);
  const [billingsLoading, setBillingsLoading] = useState(false);
  const [billingSummaryExpanded, setBillingSummaryExpanded] = useState(false);

  // Seleção múltipla de boletos (soma para pagamento em lote)
  const [selectedBillingIds, setSelectedBillingIds] = useState<number[]>([]);
  const [gerandoCarne, setGerandoCarne] = useState(false);
  // Carnês já gerados do cliente (reabrir/baixar) — carneExpandido controla
  // qual card está com o detalhe por parcela (pago/pendente) aberto.
  const [carnes, setCarnes] = useState<CarneItem[]>([]);
  const [carneExpandido, setCarneExpandido] = useState<number | null>(null);

  // Unificação de boletos (negociação: N boletos abertos → 1 avulso)
  const [unifyOpen, setUnifyOpen] = useState(false);
  const [unifyForm, setUnifyForm] = useState({ due_date: '', amount: '', notes: '' });
  const [unifying, setUnifying] = useState(false);

  // Ações do modal de boletos (alterar / histórico)
  const [editBilling, setEditBilling] = useState<BillingItem | null>(null);
  const [editBillingForm, setEditBillingForm] = useState({ amount: '', due_date: '', justification: '' });
  const [savingBilling, setSavingBilling] = useState(false);
  const [historyBilling, setHistoryBilling] = useState<BillingItem | null>(null);
  const [billingChanges, setBillingChanges] = useState<BillingChange[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Modal "Veículos onde o cliente é interveniente financeiro"
  const [intervModalOpen, setIntervModalOpen] = useState(false);
  const [intervModalClient, setIntervModalClient] = useState<Client | null>(null);
  const [intervContracts, setIntervContracts] = useState<IntervContract[]>([]);
  const [intervLoading, setIntervLoading] = useState(false);

  // Modal "Ficha de adesão / contrato" (botão teal da impressora)
  const [contractSheetOpen, setContractSheetOpen] = useState(false);
  const [contractSheetClient, setContractSheetClient] = useState<Client | null>(null);
  const [contractSheetLoading, setContractSheetLoading] = useState(false);
  // Contrato assinado guardado nos documentos (categoria 'contrato')
  const [contractDocs, setContractDocs] = useState<ClientDocument[]>([]);
  const [contractSheetItems, setContractSheetItems] = useState<ContractSheetItem[]>([]);
  const [contractSignAlvo, setContractSignAlvo] = useState('');   // contrato que o assinado enviado coloca "em vigor"
  const [contractFile, setContractFile] = useState<File | null>(null);
  const [uploadingContract, setUploadingContract] = useState(false);
  const [contractCheck, setContractCheck] = useState<{ level: string; message: string } | null>(null);

  // Modal "Notas fiscais do cliente" (botão da patinha)
  const [nfseModalOpen, setNfseModalOpen] = useState(false);
  const [nfseModalClient, setNfseModalClient] = useState<Client | null>(null);
  const [clientNotas, setClientNotas] = useState<NfseItem[]>([]);
  const [nfseLoading, setNfseLoading] = useState(false);

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

  function openVehiclesModal(client: Client) {
    setVehiclesModalClient(client);
    setVehiclesModalOpen(true);
  }

  const vehiclesDetailedQuery = useClientVehiclesDetailedQuery(
    token,
    vehiclesModalOpen ? vehiclesModalClient?.id ?? null : null,
  );

  async function openBillingsModal(client: Client) {
    setBillingsModalClient(client);
    setBillingsModalOpen(true);
    setSelectedBillingIds([]);
    setBillingsLoading(true);
    try {
      const [data, cs] = await Promise.all([
        apiFetch<BillingItem[]>(`/billings?client_id=${client.id}&limit=100`, {}, token!).catch(() => []),
        apiFetch<CarneItem[]>(`/boletos/carne?client_id=${client.id}`, {}, token!).catch(() => []),
      ]);
      setClientBillings(data);
      setCarnes(cs);
    } finally {
      setBillingsLoading(false);
    }
  }

  async function reloadCarnes() {
    if (!token || !billingsModalClient) return;
    const cs = await apiFetch<CarneItem[]>(`/boletos/carne?client_id=${billingsModalClient.id}`, {}, token).catch(() => []);
    setCarnes(cs);
  }

  async function baixarCarne(loteId: number) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/carne/${loteId}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      entregarArquivo(await resp.blob(), `carne-${loteId}.pdf`, { emNovaAba: true });
    } catch (err) {
      alert(parseError(err));
    }
  }

  // Valor com multa/juros calculado pelo BACKEND (fonte única — cláusula 4.3
  // do contrato). Vem no campo valor_com_juros do /billings.
  async function reloadClientBillings() {
    if (!token || !billingsModalClient) return;
    const data = await apiFetch<BillingItem[]>(
      `/billings?client_id=${billingsModalClient.id}&limit=100`, {}, token
    ).catch(() => []);
    setClientBillings(data);
  }

  function openUnifyModal() {
    const sel = clientBillings.filter((b) => selectedBillingIds.includes(b.id));
    // Ponto de partida da negociação é o valor JÁ atualizado com juros (o
    // mesmo "Total com juros" mostrado na barra de seleção) — o operador
    // ajusta a partir dele pra dar desconto ou arredondar, não da soma nominal.
    const somaComJuros = sel.reduce((s, b) => s + (valorComJuros(b) ?? b.amount), 0);
    setUnifyForm({ due_date: '', amount: somaComJuros.toFixed(2), notes: '' });
    setUnifyOpen(true);
  }

  /**
   * Gera o carnê das cobranças selecionadas: registra o lote na Ailos,
   * aguarda o processamento (assíncrono) e baixa o PDF. As parcelas são as
   * cobranças em aberto do mesmo cliente, na ordem selecionada.
   */
  async function gerarCarne() {
    if (!token || selectedBillingIds.length < 2) return;
    const qtd = selectedBillingIds.length;
    if (!confirm(`Gerar o carnê registra ${qtd} boletos reais na Ailos (um por parcela). Continuar?`)) return;
    setGerandoCarne(true);
    try {
      // 1. registra o carnê (assíncrono — devolve o lote com ticket)
      const lote = await apiFetch<{ id: number; ticket: string; status: string }>(
        '/ailos/carne/lote',
        { method: 'POST', body: JSON.stringify({ billing_ids: selectedBillingIds }) },
        token,
      );
      // 2. aguarda o lote sair de "processing" (cada consulta atualiza os boletos)
      let status = lote.status;
      for (let i = 0; i < 15 && status === 'processing'; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await apiFetch<{ status: string }>(`/ailos/lotes/${lote.ticket}`, {}, token).catch(() => ({ status } as { status: string }));
        status = st.status;
      }
      // O carnê já foi REGISTRADO no passo 1. Se ainda processa, não insistir no
      // download (evita re-registro, que duplicaria boletos).
      if (status === 'processing') {
        setSelectedBillingIds([]);
        await reloadCarnes();
        setFeedback(`Carnê registrado (lote #${lote.id}). As parcelas ainda estão sendo processadas na Ailos — ele já aparece em "Carnês gerados"; baixe o PDF em instantes.`);
        return;
      }
      // 3. baixa o PDF do carnê (só as parcelas já registradas entram)
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/carne/${lote.id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(`Carnê registrado (lote #${lote.id}), mas o PDF ainda não saiu: ${detalhe}`);
      }
      entregarArquivo(await resp.blob(), `carne-${lote.id}.pdf`, { emNovaAba: true });
      setSelectedBillingIds([]);
      setFeedback(`Carnê gerado com ${qtd} parcela(s).`);
      await Promise.all([reloadClientBillings(), reloadCarnes()]);
    } catch (err) {
      alert(parseError(err));
    } finally {
      setGerandoCarne(false);
    }
  }

  async function saveUnify() {
    if (!token || selectedBillingIds.length < 2) return;
    if (!unifyForm.due_date) { alert('Informe o vencimento do boleto único.'); return; }
    setUnifying(true);
    try {
      const nova = await apiFetch<BillingItem>('/billings/unificar', {
        method: 'POST',
        body: JSON.stringify({
          billing_ids: selectedBillingIds,
          due_date: unifyForm.due_date,
          amount: unifyForm.amount ? Number(unifyForm.amount) : undefined,
          notes: unifyForm.notes.trim() || undefined,
        }),
      }, token);
      setUnifyOpen(false);
      setSelectedBillingIds([]);
      setFeedback(`Boleto único #${nova.id} criado. As cobranças originais foram canceladas.`);
      await reloadClientBillings();
    } catch (err) {
      alert(parseError(err));
    } finally {
      setUnifying(false);
    }
  }

  function openEditBilling(b: BillingItem) {
    setEditBilling(b);
    setEditBillingForm({ amount: String(b.amount), due_date: b.due_date, justification: '' });
  }

  async function saveEditBilling() {
    if (!token || !editBilling) return;
    const payload: Record<string, unknown> = {};
    if (Number(editBillingForm.amount) !== editBilling.amount) payload.amount = Number(editBillingForm.amount);
    if (editBillingForm.due_date !== editBilling.due_date) payload.due_date = editBillingForm.due_date;
    if (Object.keys(payload).length === 0) { setEditBilling(null); return; }
    const justification = editBillingForm.justification.trim();
    if (!justification) { alert('Informe a justificativa da alteração.'); return; }
    payload.justification = justification;
    setSavingBilling(true);
    try {
      await apiFetch(`/billings/${editBilling.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
      setEditBilling(null);
      await reloadClientBillings();
    } catch (err) {
      alert(parseError(err));
    } finally {
      setSavingBilling(false);
    }
  }

  async function openBillingHistory(b: BillingItem) {
    if (!token) return;
    setHistoryBilling(b);
    setHistoryLoading(true);
    try {
      const logs = await apiFetch<BillingChange[]>(`/billings/${b.id}/changes`, {}, token).catch(() => []);
      setBillingChanges(logs);
    } finally {
      setHistoryLoading(false);
    }
  }

  // Envio com template das Configurações — lógica compartilhada em lib/boleto-mensagem
  async function sendBoletoEmail(b: BillingItem) {
    if (!token || !billingsModalClient) return;
    try {
      await enviarBoletoEmail(b, billingsModalClient, token);
    } catch (err) {
      alert(parseError(err));
    }
  }

  async function sendBoletoWhats(b: BillingItem) {
    if (!token || !billingsModalClient) return;
    try {
      await enviarBoletoWhats(b, billingsModalClient, token);
    } catch (err) {
      alert(parseError(err));
    }
  }

  /** Abre o PDF da nota fiscal no navegador (montado a partir do XML). */
  async function abrirNotaPdf(billingId: number) {
    if (!token) return;
    try {
      const resp = await fetch(
        `${API_URL.replace(/\/+$/, '')}/nfse/${billingId}/danfse-local`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!resp.ok) {
        let detalhe = `Erro ${resp.status}`;
        try { detalhe = (await resp.json())?.detail || detalhe; } catch { /* noop */ }
        throw new Error(detalhe);
      }
      entregarArquivo(await resp.blob(), `nfse-${billingId}.pdf`, { emNovaAba: true });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao abrir a nota fiscal');
    }
  }

  async function baixarComprovante(b: BillingItem) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/billings/${b.id}/receipt`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status} ao gerar o comprovante`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao gerar comprovante');
    }
  }

  async function baixarBoletoPdf(b: BillingItem) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/boletos/${b.id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Nome do arquivo: cliente + vencimento (ex.: "EUNICE SOUSA SIMAS 28-08-2026.pdf").
      a.download = `${nomeArquivoCliente(billingsModalClient?.name, b.due_date)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao baixar boleto');
    }
  }

  async function openIntervenienteModal(client: Client) {
    setIntervModalClient(client);
    setIntervModalOpen(true);
    setIntervLoading(true);
    try {
      const data = await apiFetch<IntervContract[]>(
        `/contracts?interveniente_client_id=${client.id}&limit=100`, {}, token!
      ).catch(() => []);
      setIntervContracts(data);
    } finally {
      setIntervLoading(false);
    }
  }

  async function openNfseModal(client: Client) {
    setNfseModalClient(client);
    setNfseModalOpen(true);
    setNfseLoading(true);
    try {
      const data = await apiFetch<NfseItem[]>(
        `/nfse?client_id=${client.id}&limit=100`, {}, token!
      ).catch(() => []);
      setClientNotas(data);
    } finally {
      setNfseLoading(false);
    }
  }

  // Abre a modal com todos os contratos do cliente (vigência, situação,
  // ver/imprimir e excluir). Substitui a impressão direta do mais recente.
  async function openContractSheet(client: Client) {
    setContractSheetClient(client);
    setContractSheetOpen(true);
    setContractSheetLoading(true);
    setContractFile(null);
    setContractCheck(null);
    setContractSheetItems([]);
    try {
      const [docs, contratos] = await Promise.all([
        apiFetch<ClientDocument[]>(`/clients/${client.id}/documents`, {}, token!).catch(() => []),
        apiFetch<ContractSheetItem[]>(`/contracts?client_id=${client.id}&limit=200`, {}, token!).catch(() => []),
      ]);
      setContractDocs(docs.filter((d) => d.category === 'contrato'));
      setContractSheetItems(contratos);
      // Sugere colocar "em vigor" o primeiro contrato ainda não assinado.
      const pendente = contratos.find((c) => !c.signed && c.status !== 'cancelado' && c.status !== 'encerrado');
      setContractSignAlvo(pendente ? String(pendente.id) : '');
    } finally {
      setContractSheetLoading(false);
    }
  }

  // Envia o contrato assinado já na categoria certa ('contrato'), direto da
  // modal — sem depender de o operador lembrar de trocar a categoria na edição.
  async function uploadSignedContract() {
    if (!token || !contractSheetClient || !contractFile) return;
    setUploadingContract(true);
    try {
      // 1. Confere o arquivo (não bloqueante). Se cair, segue o upload assim mesmo.
      let verdict: { level: string; message: string } | null = null;
      try {
        const vbody = new FormData();
        vbody.append('file', contractFile);
        verdict = await apiFetch<{ level: string; message: string }>(
          `/contracts/validate-signed?client_id=${contractSheetClient.id}`,
          { method: 'POST', body: vbody }, token,
        );
      } catch { verdict = null; }
      setContractCheck(verdict && verdict.message ? verdict : null);
      // "em branco" (não preenchido) e "mismatch" (arquivo errado) pedem
      // confirmação — fica opcional seguir. Escaneamento ilegível só avisa.
      if (verdict && (verdict.level === 'blank' || verdict.level === 'mismatch')) {
        if (!window.confirm(`${verdict.message}\n\nDeseja enviar mesmo assim?`)) {
          setUploadingContract(false);
          return;
        }
      }
      // 2. Sobe o arquivo já na categoria certa.
      const body = new FormData();
      body.append('category', 'contrato');
      body.append('files', contractFile);
      await apiFetch(`/clients/${contractSheetClient.id}/documents`, { method: 'POST', body }, token);
      setContractFile(null);
      const docs = await apiFetch<ClientDocument[]>(`/clients/${contractSheetClient.id}/documents`, {}, token).catch(() => []);
      setContractDocs(docs.filter((d) => d.category === 'contrato'));
      setContractSheetClient((prev) => (prev ? { ...prev, contrato_armazenado: true } : prev));
      // 3. O contrato escolhido passa a valer ("em vigor") — assinado recebido.
      if (contractSignAlvo) {
        await apiFetch(`/contracts/${contractSignAlvo}`, { method: 'PUT', body: JSON.stringify({ signed: true }) }, token).catch(() => null);
        const contratos = await apiFetch<ContractSheetItem[]>(`/contracts?client_id=${contractSheetClient.id}&limit=200`, {}, token).catch(() => []);
        setContractSheetItems(contratos);
        const pendente = contratos.find((c) => !c.signed && c.status !== 'cancelado' && c.status !== 'encerrado');
        setContractSignAlvo(pendente ? String(pendente.id) : '');
      }
      await invalidateClients(); // atualiza o selo "armazenado" na listagem
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao enviar o contrato assinado');
    } finally {
      setUploadingContract(false);
    }
  }

  async function removeContractDoc(id: number) {
    if (!token || !contractSheetClient) return;
    if (!window.confirm('Remover este contrato assinado?')) return;
    try {
      await apiFetch(`/clients/${contractSheetClient.id}/documents/${id}`, { method: 'DELETE' }, token);
      const docs = await apiFetch<ClientDocument[]>(`/clients/${contractSheetClient.id}/documents`, {}, token).catch(() => []);
      const contratoDocs = docs.filter((d) => d.category === 'contrato');
      setContractDocs(contratoDocs);
      setContractSheetClient((prev) => (prev ? { ...prev, contrato_armazenado: contratoDocs.length > 0 } : prev));
      await invalidateClients();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao remover o contrato assinado');
    }
  }

  // Abre o PDF do contrato (registro) gerado a partir dos dados dele.
  async function baixarContrato(id: number) {
    if (!token) return;
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/contracts/${id}/pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`Erro ${resp.status} ao gerar o contrato`);
      entregarArquivo(await resp.blob(), `contrato-${id}.pdf`, { emNovaAba: true });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao abrir o contrato');
    }
  }

  // Cancela/exclui um contrato (soft delete: vira "cancelado" e some das listagens).
  async function excluirContrato(id: number) {
    if (!token) return;
    if (!window.confirm('Cancelar/excluir este contrato? Ele passa a "cancelado" e some das listagens.')) return;
    try {
      await apiFetch(`/contracts/${id}`, { method: 'DELETE' }, token);
      setContractSheetItems((prev) => prev.filter((c) => c.id !== id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Erro ao excluir o contrato');
    }
  }

  async function deleteClient(client: Client) {
    if (!token) return;
    const ok = window.confirm(
      `Excluir o cliente "${client.name}"?\n\nO cadastro será inativado (soft delete) e some das listagens.`
    );
    if (!ok) return;
    try {
      await apiFetch(`/clients/${client.id}`, { method: 'DELETE' }, token);
      setFeedback('Cliente removido.');
      await invalidateClients();
    } catch (err) {
      setError(parseError(err));
    }
  }

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
      rg_ie: client.rg_ie || '',
      birth_date: client.birth_date || '',
      em1_name: client.emergency_contacts?.[0]?.name || '',
      em1_phone: client.emergency_contacts?.[0]?.phone || '',
      em1_mobile: client.emergency_contacts?.[0]?.mobile || '',
      em2_name: client.emergency_contacts?.[1]?.name || '',
      em2_phone: client.emergency_contacts?.[1]?.phone || '',
      em2_mobile: client.emergency_contacts?.[1]?.mobile || '',
      boleto_format: client.boleto_format || 'unico',
      boleto_fee: client.boleto_fee || 'nao',
      issue_invoice: client.issue_invoice || 'sim',
      tributacao: client.tributacao || 'dentro_municipio',
      iss_retido: client.iss_retido || 'nao',
      optante_simples: client.optante_simples || 'sim',
      delivery_method: client.delivery_method || 'email',
      send_boleto_whatsapp: !!client.send_boleto_whatsapp,
      trade_name: client.trade_name || '',
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

      const emergencyContacts = [
        { name: form.em1_name.trim(), phone: form.em1_phone.trim(), mobile: form.em1_mobile.trim() },
        { name: form.em2_name.trim(), phone: form.em2_phone.trim(), mobile: form.em2_mobile.trim() },
      ]
        .filter((e) => e.name || e.phone || e.mobile)
        .map((e) => ({ name: e.name || null, phone: e.phone || null, mobile: e.mobile || null }));

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
        rg_ie: form.rg_ie.trim() || null,
        birth_date: form.birth_date || null,
        emergency_contacts: emergencyContacts.length ? emergencyContacts : null,
        boleto_format: form.boleto_format || null,
        boleto_fee: form.boleto_fee || null,
        issue_invoice: form.issue_invoice || null,
        tributacao: form.tributacao || null,
        iss_retido: form.iss_retido || null,
        optante_simples: form.optante_simples || null,
        delivery_method: form.delivery_method || null,
        send_boleto_whatsapp: form.send_boleto_whatsapp,
        trade_name: form.trade_name.trim() || null,
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
      await invalidateClients();
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
      {(guardError || error || listError || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error || listError) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error || listError}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}

      {/* Base de clientes primeiro; os indicadores ficam abaixo (pedido do cliente) */}
      <section>
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Base de clientes"
            description="Pesquise e gerencie a carteira completa de clientes."
            actions={
              <div className="flex items-center gap-2">
                {token && <ExportButton path="exports/clients" basename="clientes" token={token} params={{ status: statusFilter, type: typeFilter }} />}
                {canEdit && <Button type="button" onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button>}
              </div>
            }
          />
          {/* Filtros à esquerda, busca à direita. Os controles têm largura fixa
              e `shrink-0` para não esticarem/espremerem conforme o conteúdo. */}
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <Select
              value={String(pageSize)}
              onChange={(e) => { setPageSize(Number(e.target.value)); pg.setPage(1); }}
              className="w-[72px] shrink-0"
              aria-label="Resultados por página"
            >
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </Select>
            <span className="shrink-0 whitespace-nowrap text-sm text-slate-500 dark:text-slate-400">
              por página
            </span>

            <Select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-44 shrink-0"
              aria-label="Filtrar por status"
            >
              <option value="">Todos os status</option>
              <option value="ativo">Ativo</option>
              <option value="inativo">Inativo</option>
              <option value="inadimplente">Inadimplente</option>
              <option value="suspenso">Suspenso</option>
            </Select>
            <Select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="w-40 shrink-0"
              aria-label="Filtrar por tipo"
            >
              <option value="">Todos os tipos</option>
              <option value="pf">Pessoa física</option>
              <option value="pj">Pessoa jurídica</option>
            </Select>
            <Button
              type="button"
              variant="secondary"
              className="shrink-0"
              onClick={() => { clientsQuery.refetch(); vehicleSummariesQuery.refetch(); }}
              disabled={loading}
            >
              {loading ? 'Atualizando…' : 'Atualizar'}
            </Button>

            <div className="relative ml-auto w-full sm:w-80">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <Input
                placeholder="Pesquisar por nome, CPF/CNPJ ou e-mail"
                value={search}
                onChange={(e) => { setSearch(e.target.value); pg.setPage(1); }}
                className="w-full pl-9"
              />
            </div>
          </div>

          <div className="mt-4">
            {loading ? (
              <TableSkeleton rows={7} cols={5} />
            ) : (error || listError) ? (
              // Falha ao carregar não é a mesma coisa que "zero clientes" — o
              // banner de erro acima já explica o problema; aqui só evitamos o
              // CTA "cadastre o primeiro cliente" que contradiria o erro.
              <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os clientes" description="Veja o erro acima e tente novamente." />
            ) : clients.length === 0 ? (
              <EmptyState icon={Users} title="Nenhum cliente encontrado" description="Ajuste os filtros ou cadastre o primeiro cliente." action={canEdit ? <Button onClick={openCreateModal} className="gap-2"><Plus className="h-4 w-4" />Adicionar cliente</Button> : undefined} />
            ) : (
              <>
              <Table>
                <TableHead>
                  <SortTh field="id" label="Matrícula" sort={clientSort} onSort={toggleClientSort} className="w-24" />
                  <SortTh field="name" label="Nome" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="trade_name" label="Nome fantasia" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="cpf_cnpj" label="CPF/CNPJ" sort={clientSort} onSort={toggleClientSort} />
                  <SortTh field="status" label="Situação" sort={clientSort} onSort={toggleClientSort} />
                  <Th className="w-28">Contrato</Th>
                  <Th className="w-40">Ações</Th>
                </TableHead>
                <TableBody>
                  {pg.slice.map((client) => {
                    const vehicles = vehiclesByClient[client.id] || [];
                    return (
                      <Tr key={client.id}>
                        <Td className="text-sm text-slate-500">{client.id}</Td>
                        <Td>
                          <p className="font-medium text-slate-900 dark:text-white">{client.name}</p>
                          <p className="text-xs text-slate-400">{client.type === 'pj' ? 'Pessoa Jurídica' : 'Pessoa Física'} · {vehicles.length} veículo(s)</p>
                        </Td>
                        <Td className="text-sm">{client.trade_name || '—'}</Td>
                        <Td>
                          <p className="font-mono text-xs">{formatCpfCnpj(client.cpf_cnpj)}</p>
                          <p className="text-xs text-slate-400">{client.email || (client.phone ? formatPhone(client.phone) : '')}</p>
                        </Td>
                        <Td>
                          <Badge variant={statusVariant(client.status)}>{statusLabel(client.status)}</Badge>
                        </Td>
                        <Td>
                          {client.contrato_armazenado
                            ? <Badge variant="success">Armazenado</Badge>
                            : <Badge variant="warning">Pendente</Badge>}
                        </Td>
                        <Td>
                          <div className="flex justify-end gap-1">
                            {/* 1. Roxo — veículos próprios do cliente */}
                            <ActionBtn color="purple" icon={Car} title="Veículos vinculados ao cliente" onClick={() => openVehiclesModal(client)} />
                            {canFinance && (
                              <>
                                {/* 2. Amarelo — veículos onde é interveniente financeiro */}
                                <ActionBtn color="yellow" icon={Coins} title="Veículos onde é interveniente financeiro" onClick={() => openIntervenienteModal(client)} />
                                {/* 3. Verde — central financeira / boletos */}
                                <ActionBtn color="green" icon={DollarSign} title="Central financeira / boletos do cliente" onClick={() => openBillingsModal(client)} />
                                {/* 4. Branco (patinha) — notas fiscais do cliente */}
                                <ActionBtn color="white" icon={PawPrint} title="Notas fiscais do cliente" onClick={() => openNfseModal(client)} />
                                {/* 5. Teal — ficha de adesão / contrato (abre a lista) */}
                                <ActionBtn color="teal" icon={Printer} title="Ficha de adesão / contrato" onClick={() => openContractSheet(client)} />
                              </>
                            )}
                            {canEdit && (
                              <>
                                {/* 6. Azul — editar */}
                                <ActionBtn color="blue" icon={Pencil} title="Editar cliente" onClick={() => openEditModal(client)} />
                                {/* 7. Vermelho — excluir/inativar */}
                                <ActionBtn color="red" icon={Trash2} title="Excluir cliente" onClick={() => deleteClient(client)} />
                              </>
                            )}
                          </div>
                        </Td>
                      </Tr>
                    );
                  })}
                </TableBody>
              </Table>

              {/* Rodapé: contagem + paginação (padrão do sistema de referência) */}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {pg.total === 0
                    ? 'Nenhum registro'
                    : `Mostrando de ${pg.start} até ${pg.end} de ${pg.total} registro(s)`}
                </p>
              </div>
              <Pagination {...pg} onPage={pg.setPage} className="mt-1" />
              </>
            )}
          </div>
        </Card>
      </section>

      {/* Indicadores da base (abaixo da tabela, conforme solicitado) */}
      <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Clientes cadastrados" value={stats.total}      hint="Base total disponível"           icon={<Users className="h-5 w-5" />} />
        <StatCard label="Clientes ativos"      value={stats.active}     hint="Cadastros em operação"  tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
        <StatCard label="Inadimplentes"        value={stats.delinquent} hint="Exigem ação do financeiro" tone="warning" icon={<AlertTriangle className="h-5 w-5" />} />
        <StatCard label="Empresas (PJ)"        value={stats.company}    hint="Cadastros PJ na base"    tone="brand"   icon={<Building2 className="h-5 w-5" />} />
      </section>

      {/* Modal de detalhes */}
      <ClientDetailModal
        open={detailsOpen}
        client={selectedClient}
        vehicles={selectedClient ? vehiclesByClient[selectedClient.id] || [] : []}
        tab={detailsTab}
        onTabChange={setDetailsTab}
        onClose={() => { setDetailsOpen(false); setSelectedClient(null); }}
        timelineLoading={timelineLoading}
        timeline={clientTimeline}
        onExportTimelinePdf={downloadTimelinePdf}
        canEdit={canEdit}
        docCategory={docCategory}
        onDocCategoryChange={setDocCategory}
        onDocFilesChange={setDocFiles}
        uploadingDocs={uploading}
        hasFilesSelected={docFiles.length > 0}
        onUploadDocs={uploadDocuments}
        documents={clientDocuments}
        onReviewDocument={reviewDocument}
        onDeleteDocument={deleteDocument}
      />

      <ClientFormModal
        open={modalOpen}
        isEditing={isEditing}
        error={modalError}
        saving={saving}
        canEdit={canEdit}
        form={form}
        onFieldChange={handleChange}
        onFormPatch={setForm}
        onAddContact={addContact}
        onRemoveContact={removeContact}
        onUpdateContact={updateContact}
        onZipBlur={fillAddressFromCep}
        onBuscarCep={() => fillAddressFromCep(form.zip_code)}
        lookingUpCep={lookingUpCep}
        docCategory={docCategory}
        onDocCategoryChange={setDocCategory}
        onDocFilesChange={setDocFiles}
        onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }}
        onCancel={() => { setModalOpen(false); resetForm(); }}
        onSubmit={submitClient}
      />

      <VehiclesModal
        open={vehiclesModalOpen}
        clientName={vehiclesModalClient?.name}
        loading={vehiclesDetailedQuery.isLoading}
        vehicles={vehiclesDetailedQuery.data ?? []}
        onClose={() => { setVehiclesModalOpen(false); setVehiclesModalClient(null); }}
      />

      <IntervenienteModal
        open={intervModalOpen}
        clientName={intervModalClient?.name}
        loading={intervLoading}
        contracts={intervContracts}
        onClose={() => { setIntervModalOpen(false); setIntervModalClient(null); setIntervContracts([]); }}
      />

      <NfseModal
        open={nfseModalOpen}
        clientName={nfseModalClient?.name}
        loading={nfseLoading}
        notas={clientNotas}
        onClose={() => { setNfseModalOpen(false); setNfseModalClient(null); setClientNotas([]); }}
        onVerPdf={abrirNotaPdf}
      />

      <ContractSheetModal
        open={contractSheetOpen}
        client={contractSheetClient}
        loading={contractSheetLoading}
        items={contractSheetItems}
        docs={contractDocs}
        signAlvo={contractSignAlvo}
        file={contractFile}
        check={contractCheck}
        uploading={uploadingContract}
        canEdit={canEdit}
        onClose={() => { setContractSheetOpen(false); setContractSheetClient(null); setContractDocs([]); setContractSheetItems([]); setContractSignAlvo(''); setContractFile(null); setContractCheck(null); }}
        onSignAlvoChange={setContractSignAlvo}
        onFileChange={(file) => { setContractFile(file); setContractCheck(null); }}
        onUpload={uploadSignedContract}
        onView={baixarContrato}
        onDeleteContract={excluirContrato}
        onDeleteDoc={removeContractDoc}
      />

      <BillingsModal
        open={billingsModalOpen}
        clientName={billingsModalClient?.name}
        loading={billingsLoading}
        billings={clientBillings}
        carnes={carnes}
        carneExpandido={carneExpandido}
        summaryExpanded={billingSummaryExpanded}
        selectedIds={selectedBillingIds}
        gerandoCarne={gerandoCarne}
        onClose={() => { setBillingsModalOpen(false); setBillingsModalClient(null); setClientBillings([]); setCarnes([]); setBillingSummaryExpanded(false); setSelectedBillingIds([]); }}
        onToggleSummary={() => setBillingSummaryExpanded((p) => !p)}
        onSelectedIdsChange={setSelectedBillingIds}
        onToggleCarne={(loteId) => setCarneExpandido((prev) => (prev === loteId ? null : loteId))}
        onBaixarCarne={baixarCarne}
        onOpenUnify={openUnifyModal}
        onGerarCarne={gerarCarne}
        onEditBilling={openEditBilling}
        onBillingHistory={openBillingHistory}
        onSendEmail={sendBoletoEmail}
        onSendWhats={sendBoletoWhats}
        onBaixarPdf={baixarBoletoPdf}
        onBaixarComprovante={baixarComprovante}
      />

      {/* ══ Modal: Unificar boletos em um único (negociação) ═══════════════ */}
      <UnifyBillingModal
        open={unifyOpen}
        selectedCount={selectedBillingIds.length}
        form={unifyForm}
        saving={unifying}
        onFormChange={setUnifyForm}
        onClose={() => setUnifyOpen(false)}
        onSave={saveUnify}
      />

      <EditBillingModal
        billing={editBilling}
        form={editBillingForm}
        saving={savingBilling}
        onFormChange={setEditBillingForm}
        onClose={() => setEditBilling(null)}
        onSave={saveEditBilling}
      />

      <BillingHistoryModal
        billing={historyBilling}
        loading={historyLoading}
        changes={billingChanges}
        onClose={() => { setHistoryBilling(null); setBillingChanges([]); }}
      />
    </PageShell>
  );
}
