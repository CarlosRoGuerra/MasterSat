'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { TrendingUp, Receipt, AlertTriangle, FileText, Wallet, CheckCircle2, Clock } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { BarChart } from '@/components/ui/bar-chart';
import { API_URL, apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type BillingInterval = 1 | 3 | 6 | 12;
type BillingStatus = 'pendente' | 'paga' | 'vencida' | 'cancelada';

type ClientOption = { id: number; name: string; cpf_cnpj: string };
type VehicleOption = { id: number; client_id: number; plate: string; model?: string | null };
type TrackerOption = { id: number; client_id?: number | null; vehicle_id?: number | null; imei: string; model?: string | null; brand?: string | null };
type Plan = { id: number; name: string; price: number; description?: string | null; active: boolean; billing_interval_months: BillingInterval; active_contracts: number };
type ServiceProduct = { id: number; name: string; category: string; default_price: number; description?: string | null; active: boolean; allow_installments: boolean; remove_after_payment: boolean; auto_add_on_uninstall: boolean };
type Contract = { id: number; client_id: number; plan_id: number; vehicle_id?: number | null; tracker_id?: number | null; start_date: string; end_date?: string | null; status: string; billing_day?: number | null; payment_method?: string | null; notes?: string | null; client_name?: string | null; plan_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; monthly_value?: number | null; open_billings: number; next_due_date?: string | null };
type ChargeItem = { id: number; client_id: number; contract_id?: number | null; vehicle_id?: number | null; tracker_id?: number | null; service_product_id?: number | null; title: string; description?: string | null; quantity: number; unit_price: number; total_amount: number; installment_count: number; start_date: string; active: boolean; remove_after_payment: boolean; completed_at?: string | null; status: string; client_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; service_product_name?: string | null; open_installments: number };
type Billing = { id: number; contract_id?: number | null; client_id: number; item_id?: number | null; vehicle_id?: number | null; tracker_id?: number | null; title?: string | null; billing_type: string; installment_number?: number | null; installment_total?: number | null; amount: number; due_date: string; status: BillingStatus; payment_date?: string | null; payment_method?: string | null; notes?: string | null; paid_amount?: number | null; receipt_number?: string | null; period_label?: string | null; client_name?: string | null; vehicle_plate?: string | null; tracker_identifier?: string | null; plan_name?: string | null; contract_status?: string | null; overdue_days: number };
type Summary = { active_plans: number; active_contracts: number; pending_billings: number; overdue_billings: number; pending_amount: number; overdue_amount: number; paid_this_month: number };
type RevenueItem = { label: string; total_received: number; total_billed: number; total_outstanding: number };
type DelinquentItem = { client_id: number; client_name: string; total_open: number; overdue_count: number };

type PlanFormState = { name: string; price: string; description: string; active: boolean; billing_interval_months: string };
type ServiceProductFormState = { name: string; category: string; default_price: string; description: string; active: boolean; allow_installments: boolean; remove_after_payment: boolean; auto_add_on_uninstall: boolean };
type ContractFormState = { client_id: string; vehicle_id: string; tracker_id: string; plan_id: string; start_date: string; end_date: string; billing_day: string; payment_method: string; notes: string; auto_generate_billings: boolean; billing_cycles: string };
type ChargeItemFormState = { client_id: string; contract_id: string; vehicle_id: string; tracker_id: string; service_product_id: string; title: string; description: string; quantity: string; unit_price: string; installment_count: string; start_date: string; remove_after_payment: boolean };
type ReceiveFormState = { paid_amount: string; payment_date: string; payment_method: string; notes: string };
type AdjustFormState = { amount: string; due_date: string; justification: string };

const fieldClass = 'w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400';
const areaClass = `${fieldClass} min-h-[96px] resize-y`;
const initialPlanForm: PlanFormState = { name: '', price: '', description: '', active: true, billing_interval_months: '1' };
const initialProductForm: ServiceProductFormState = { name: '', category: 'servico', default_price: '', description: '', active: true, allow_installments: true, remove_after_payment: false, auto_add_on_uninstall: false };
const initialContractForm: ContractFormState = { client_id: '', vehicle_id: '', tracker_id: '', plan_id: '', start_date: new Date().toISOString().slice(0, 10), end_date: '', billing_day: '', payment_method: 'boleto', notes: '', auto_generate_billings: true, billing_cycles: '12' };
const initialChargeItemForm: ChargeItemFormState = { client_id: '', contract_id: '', vehicle_id: '', tracker_id: '', service_product_id: '', title: '', description: '', quantity: '1', unit_price: '', installment_count: '1', start_date: new Date().toISOString().slice(0, 10), remove_after_payment: false };
const initialReceiveForm: ReceiveFormState = { paid_amount: '', payment_date: new Date().toISOString().slice(0, 10), payment_method: 'pix', notes: '' };
const initialAdjustForm: AdjustFormState = { amount: '', due_date: '', justification: '' };

function parseError(error: unknown) { return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.'; }
function formatCurrency(value: number) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value || 0); }
function intervalLabel(months: number) { return ({ 1: 'Mensal', 3: 'Trimestral', 6: 'Semestral', 12: 'Anual' } as Record<number, string>)[months] || `${months} meses`; }
function statusBadge(status: string) { switch (status) { case 'paga': return 'border-emerald-200 bg-emerald-50 text-emerald-700'; case 'vencida': return 'border-rose-200 bg-rose-50 text-rose-700'; case 'cancelada': return 'border-amber-200 bg-amber-50 text-amber-700'; default: return 'border-slate-200 bg-slate-50 text-slate-700'; } }
async function downloadProtectedFile(path: string, token: string, filename: string): Promise<void> {
  const url = `${API_URL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
  let response: Response;
  try {
    response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new Error('Não foi possível conectar ao servidor. Verifique se o backend está em execução.');
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { const data = await response.json(); detail = data?.detail || detail; } catch { try { detail = await response.text() || detail; } catch { /* noop */ } }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}



function cardKeyHandler(event: React.KeyboardEvent<HTMLElement>, callback: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    callback();
  }
}

export default function FinanceiroPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(['admin', 'financeiro'], '/login/admin');
  const canEdit = !!user && (user.role === 'admin' || user.role === 'financeiro');

  const [plans, setPlans] = useState<Plan[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [trackers, setTrackers] = useState<TrackerOption[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [serviceProducts, setServiceProducts] = useState<ServiceProduct[]>([]);
  const [chargeItems, setChargeItems] = useState<ChargeItem[]>([]);
  const [billings, setBillings] = useState<Billing[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [revenue, setRevenue] = useState<RevenueItem[]>([]);
  const [delinquents, setDelinquents] = useState<DelinquentItem[]>([]);
  const [selectedBilling, setSelectedBilling] = useState<Billing | null>(null);
  const [editingPlanId, setEditingPlanId] = useState<number | null>(null);
  const [editingProductId, setEditingProductId] = useState<number | null>(null);
  const [editingContractId, setEditingContractId] = useState<number | null>(null);
  const [planForm, setPlanForm] = useState<PlanFormState>(initialPlanForm);
  const [serviceProductForm, setServiceProductForm] = useState<ServiceProductFormState>(initialProductForm);
  const [contractForm, setContractForm] = useState<ContractFormState>(initialContractForm);
  const [chargeItemForm, setChargeItemForm] = useState<ChargeItemFormState>(initialChargeItemForm);
  const [receiveForm, setReceiveForm] = useState<ReceiveFormState>(initialReceiveForm);
  const [adjustForm, setAdjustForm] = useState<AdjustFormState>(initialAdjustForm);
  const [billingSearch, setBillingSearch] = useState('');
  const [billingStatusFilter, setBillingStatusFilter] = useState('');
  const [billingView, setBillingView] = useState<'alert' | 'all'>('alert');
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [planModal, setPlanModal] = useState(false);
  const [productModal, setProductModal] = useState(false);
  const [contractModal, setContractModal] = useState(false);
  const [chargeModal, setChargeModal] = useState(false);
  const [receiveModal, setReceiveModal] = useState(false);
  const [adjustModal, setAdjustModal] = useState(false);

  function openCreatePlan() { setEditingPlanId(null); setPlanForm(initialPlanForm); setModalError(''); setPlanModal(true); }
  function openCreateProduct() { setEditingProductId(null); setServiceProductForm(initialProductForm); setModalError(''); setProductModal(true); }
  function openCreateContract() { setEditingContractId(null); setContractForm(initialContractForm); setModalError(''); setContractModal(true); }

  function openEditPlan(plan: Plan) {
    setEditingPlanId(plan.id);
    setPlanForm({ name: plan.name, price: String(plan.price), description: plan.description || '', active: plan.active, billing_interval_months: String(plan.billing_interval_months) });
    setModalError('');
    setPlanModal(true);
  }

  function openEditProduct(product: ServiceProduct) {
    setEditingProductId(product.id);
    setServiceProductForm({
      name: product.name,
      category: product.category,
      default_price: String(product.default_price),
      description: product.description || '',
      active: product.active,
      allow_installments: product.allow_installments,
      remove_after_payment: product.remove_after_payment,
      auto_add_on_uninstall: product.auto_add_on_uninstall,
    });
    setModalError('');
    setProductModal(true);
  }

  function openEditContract(contract: Contract) {
    setEditingContractId(contract.id);
    setModalError('');
    setContractForm({
      client_id: String(contract.client_id),
      vehicle_id: contract.vehicle_id ? String(contract.vehicle_id) : '',
      tracker_id: contract.tracker_id ? String(contract.tracker_id) : '',
      plan_id: String(contract.plan_id),
      start_date: contract.start_date,
      end_date: contract.end_date || '',
      billing_day: contract.billing_day ? String(contract.billing_day) : '',
      payment_method: contract.payment_method || 'boleto',
      notes: contract.notes || '',
      auto_generate_billings: false,
      billing_cycles: '12',
    });
    setContractModal(true);
  }

  async function handleDeletePlan(plan: Plan) {
    if (!token || !canEdit) return;
    if (!window.confirm(`Deseja remover o plano ${plan.name}?`)) return;
    setProcessing(true);
    try {
      await apiFetch(`/plans/${plan.id}`, { method: 'DELETE' }, token);
      setFeedback('Plano removido com sucesso.');
      if (editingPlanId === plan.id) {
        setEditingPlanId(null);
        setPlanForm(initialPlanForm);
        setPlanModal(false);
      }
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleDeleteProduct(product: ServiceProduct) {
    if (!token || !canEdit) return;
    if (!window.confirm(`Deseja remover o item ${product.name}?`)) return;
    setProcessing(true);
    try {
      await apiFetch(`/service-products/${product.id}`, { method: 'DELETE' }, token);
      setFeedback('Serviço/produto removido com sucesso.');
      if (editingProductId === product.id) {
        setEditingProductId(null);
        setServiceProductForm(initialProductForm);
        setProductModal(false);
      }
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleDeleteContract(contract: Contract) {
    if (!token || !canEdit) return;
    if (!window.confirm(`Deseja excluir o contrato de ${contract.client_name || 'cliente'}?`)) return;
    setProcessing(true);
    try {
      await apiFetch(`/contracts/${contract.id}`, { method: 'DELETE' }, token);
      setFeedback('Contrato removido com sucesso.');
      if (editingContractId === contract.id) {
        setEditingContractId(null);
        setContractForm(initialContractForm);
        setContractModal(false);
      }
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  async function loadData(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (billingSearch) query.set('search', billingSearch);
      if (billingStatusFilter) query.set('status', billingStatusFilter);
      query.set('limit', '300');
      const [plansResponse, clientsResponse, vehiclesResponse, trackersResponse, contractsResponse, productsResponse, chargeItemsResponse, billingsResponse, summaryResponse, revenueResponse, delinquentResponse] = await Promise.all([
        apiFetch<Plan[]>('/plans', {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=300', {}, currentToken),
        apiFetch<VehicleOption[]>('/vehicles?limit=300', {}, currentToken),
        apiFetch<TrackerOption[]>('/trackers?limit=300', {}, currentToken),
        apiFetch<Contract[]>('/contracts', {}, currentToken),
        apiFetch<ServiceProduct[]>('/service-products', {}, currentToken),
        apiFetch<ChargeItem[]>('/client-charge-items', {}, currentToken),
        apiFetch<Billing[]>(`/billings?${query.toString()}`, {}, currentToken),
        apiFetch<Summary>('/billings/summary', {}, currentToken),
        apiFetch<RevenueItem[]>('/billings/reports/revenue?period=monthly', {}, currentToken),
        apiFetch<DelinquentItem[]>('/billings/reports/delinquent', {}, currentToken),
      ]);
      setPlans(plansResponse);
      setClients(clientsResponse);
      setVehicles(vehiclesResponse);
      setTrackers(trackersResponse);
      setContracts(contractsResponse);
      setServiceProducts(productsResponse);
      setChargeItems(chargeItemsResponse);
      setBillings(billingsResponse);
      setSummary(summaryResponse);
      setRevenue(revenueResponse);
      setDelinquents(delinquentResponse);
      if (selectedBilling) setSelectedBilling(billingsResponse.find((item) => item.id === selectedBilling.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!token) return;
    loadData(token);
  }, [token]);

  useEffect(() => {
    if (selectedBilling) {
      setReceiveForm({ paid_amount: String(selectedBilling.amount || ''), payment_date: new Date().toISOString().slice(0, 10), payment_method: selectedBilling.payment_method || 'pix', notes: '' });
      setAdjustForm({ amount: String(selectedBilling.amount || ''), due_date: selectedBilling.due_date || '', justification: '' });
    }
  }, [selectedBilling]);

  const visibleContracts = useMemo(() => chargeItemForm.client_id ? contracts.filter((item) => item.client_id === Number(chargeItemForm.client_id)) : contracts, [contracts, chargeItemForm.client_id]);
  const contractVehicles = useMemo(() => contractForm.client_id ? vehicles.filter((item) => item.client_id === Number(contractForm.client_id)) : vehicles, [vehicles, contractForm.client_id]);
  const contractTrackers = useMemo(() => trackers.filter((item) => (!contractForm.client_id || item.client_id === Number(contractForm.client_id)) && (!contractForm.vehicle_id || item.vehicle_id === Number(contractForm.vehicle_id) || item.vehicle_id == null)), [trackers, contractForm.client_id, contractForm.vehicle_id]);
  const chargeVehicles = useMemo(() => chargeItemForm.client_id ? vehicles.filter((item) => item.client_id === Number(chargeItemForm.client_id)) : vehicles, [vehicles, chargeItemForm.client_id]);
  const chargeTrackers = useMemo(() => trackers.filter((item) => (!chargeItemForm.client_id || item.client_id === Number(chargeItemForm.client_id)) && (!chargeItemForm.vehicle_id || item.vehicle_id === Number(chargeItemForm.vehicle_id) || item.vehicle_id == null)), [trackers, chargeItemForm.client_id, chargeItemForm.vehicle_id]);

  async function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      const payload = { name: planForm.name.trim(), price: Number(planForm.price.replace(',', '.')), description: planForm.description.trim() || null, active: planForm.active, billing_interval_months: Number(planForm.billing_interval_months) };
      if (editingPlanId) {
        await apiFetch(`/plans/${editingPlanId}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Plano atualizado com sucesso.');
      } else {
        await apiFetch('/plans', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Plano cadastrado com sucesso.');
      }
      setPlanForm(initialPlanForm);
      setEditingPlanId(null);
      setPlanModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      const payload = { name: serviceProductForm.name.trim(), category: serviceProductForm.category, default_price: Number(serviceProductForm.default_price.replace(',', '.')), description: serviceProductForm.description.trim() || null, active: serviceProductForm.active, allow_installments: serviceProductForm.allow_installments, remove_after_payment: serviceProductForm.remove_after_payment, auto_add_on_uninstall: serviceProductForm.auto_add_on_uninstall };
      if (editingProductId) {
        await apiFetch(`/service-products/${editingProductId}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Serviço/produto atualizado com sucesso.');
      } else {
        await apiFetch('/service-products', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Serviço/produto cadastrado com sucesso.');
      }
      setServiceProductForm(initialProductForm);
      setEditingProductId(null);
      setProductModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitContract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      const payload = { client_id: Number(contractForm.client_id), vehicle_id: contractForm.vehicle_id ? Number(contractForm.vehicle_id) : null, tracker_id: contractForm.tracker_id ? Number(contractForm.tracker_id) : null, plan_id: Number(contractForm.plan_id), start_date: contractForm.start_date, end_date: contractForm.end_date || null, billing_day: contractForm.billing_day ? Number(contractForm.billing_day) : null, payment_method: contractForm.payment_method || null, notes: contractForm.notes.trim() || null, auto_generate_billings: contractForm.auto_generate_billings, billing_cycles: contractForm.billing_cycles ? Number(contractForm.billing_cycles) : 12 };
      if (editingContractId) {
        await apiFetch(`/contracts/${editingContractId}`, { method: 'PUT', body: JSON.stringify(payload) }, token);
        setFeedback('Contrato atualizado com sucesso.');
      } else {
        await apiFetch('/contracts', { method: 'POST', body: JSON.stringify(payload) }, token);
        setFeedback('Contrato criado com sucesso.');
      }
      setContractForm(initialContractForm);
      setEditingContractId(null);
      setContractModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function submitChargeItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch('/client-charge-items', { method: 'POST', body: JSON.stringify({ client_id: Number(chargeItemForm.client_id), contract_id: chargeItemForm.contract_id ? Number(chargeItemForm.contract_id) : null, vehicle_id: chargeItemForm.vehicle_id ? Number(chargeItemForm.vehicle_id) : null, tracker_id: chargeItemForm.tracker_id ? Number(chargeItemForm.tracker_id) : null, service_product_id: chargeItemForm.service_product_id ? Number(chargeItemForm.service_product_id) : null, title: chargeItemForm.title.trim(), description: chargeItemForm.description.trim() || null, quantity: Number(chargeItemForm.quantity || '1'), unit_price: Number(chargeItemForm.unit_price.replace(',', '.')), installment_count: Number(chargeItemForm.installment_count || '1'), start_date: chargeItemForm.start_date, remove_after_payment: chargeItemForm.remove_after_payment }) }, token);
      setFeedback('Lançamento criado com sucesso.');
      setChargeItemForm(initialChargeItemForm);
      setChargeModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleReceive() {
    if (!token || !selectedBilling || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch(`/billings/${selectedBilling.id}/receive`, { method: 'POST', body: JSON.stringify({ paid_amount: Number(receiveForm.paid_amount.replace(',', '.')), payment_date: receiveForm.payment_date, payment_method: receiveForm.payment_method, notes: receiveForm.notes || null }) }, token);
      setFeedback('Pagamento registrado com sucesso.');
      setReceiveModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleAdjust() {
    if (!token || !selectedBilling || !canEdit) return;
    setProcessing(true);
    try {
      await apiFetch(`/billings/${selectedBilling.id}`, { method: 'PUT', body: JSON.stringify({ amount: Number(adjustForm.amount.replace(',', '.')), due_date: adjustForm.due_date, justification: adjustForm.justification.trim() }) }, token);
      setFeedback('Cobrança ajustada com sucesso.');
      setAdjustModal(false);
      await loadData(token);
    } catch (err) { setModalError(parseError(err)); } finally { setProcessing(false); }
  }

  async function handleCancel() {
    if (!token || !selectedBilling || !canEdit) return;
    const reason = window.prompt('Informe a justificativa para cancelamento:', '');
    if (!reason) return;
    setProcessing(true);
    try {
      await apiFetch(`/billings/${selectedBilling.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) }, token);
      setFeedback('Cobrança cancelada com sucesso.');
      await loadData(token);
    } catch (err) { setError(parseError(err)); } finally { setProcessing(false); }
  }

  return (
    <PageShell title="Financeiro" description="Gestão financeira mais limpa, com ações principais em modal, leitura executiva de faturamento e visão clara de inadimplência.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Recebido no mês"  value={formatCurrency(summary?.paid_this_month ?? 0)} hint="Receita consolidada"        tone="success" icon={<TrendingUp className="h-5 w-5" />} />
        <StatCard label="Pendentes"        value={summary?.pending_billings ?? 0}                  hint={formatCurrency(summary?.pending_amount ?? 0)} tone="brand"   icon={<Clock className="h-5 w-5" />} />
        <StatCard label="Vencidas"         value={summary?.overdue_billings ?? 0}                  hint={formatCurrency(summary?.overdue_amount ?? 0)} tone="danger"  icon={<AlertTriangle className="h-5 w-5" />} />
        <StatCard label="Contratos ativos" value={summary?.active_contracts ?? 0}                  hint={`${summary?.active_plans ?? 0} plano(s) em uso`}             icon={<FileText className="h-5 w-5" />} />
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          <Card>
            <SectionHeader
              eyebrow="Ações rápidas"
              title="Cadastros e lançamentos"
              description="Todas as ações principais do módulo financeiro agora ficam em modais, preservando foco visual na operação."
              actions={<>
                <Button type="button" onClick={openCreatePlan}>Novo plano</Button>
                <Button type="button" className="bg-brand-700 hover:bg-brand-600 dark:bg-cyan-400 dark:text-slate-950" onClick={openCreateProduct}>Novo serviço/produto</Button>
                <Button type="button" className="bg-slate-900 hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white" onClick={openCreateContract}>Novo contrato</Button>
                <Button type="button" onClick={() => setChargeModal(true)}>Novo lançamento</Button>
              </>}
            />
            <div className="mt-6 grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                <p className="mb-1 text-xs font-bold uppercase tracking-widest text-slate-500">Faturamento mensal</p>
                <p className="mb-3 text-xs text-slate-400">Receita recebida por mês (passe o mouse para ver o valor)</p>
                <BarChart
                  items={revenue.slice(-8).map((r) => ({ label: r.label, value: r.total_received, secondaryValue: r.total_billed }))}
                  formatValue={formatCurrency}
                  height={150}
                  emptyMessage="Sem dados de faturamento."
                />
              </div>
              <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-5 dark:border-slate-800 dark:bg-slate-950/60">
                <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-500">Clientes inadimplentes</p>
                <div className="space-y-2">
                  {delinquents.length === 0 ? (
                    <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum cliente inadimplente.</p>
                  ) : delinquents.slice(0, 5).map((item) => (
                    <div key={item.client_id} className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm dark:border-slate-800 dark:bg-slate-900">
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-slate-900 dark:text-white">{item.client_name}</p>
                        <p className="text-xs text-slate-500">{item.overdue_count} cobrança(s) vencida(s)</p>
                      </div>
                      <span className="ml-3 shrink-0 font-bold text-rose-600 dark:text-rose-400">{formatCurrency(item.total_open)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <SectionHeader
              eyebrow="Cobranças"
              title={billingView === 'alert' ? 'Carteira — atenção imediata' : 'Carteira completa'}
              description={billingView === 'alert' ? 'Vencidas e próximas de vencer (≤ 7 dias). Selecione para registrar pagamento ou ajustar.' : 'Todas as cobranças carregadas. Use os filtros para refinar a listagem.'}
              actions={
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setBillingView(billingView === 'alert' ? 'all' : 'alert')}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-600 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                  >
                    {billingView === 'alert' ? `Ver todas (${billings.length})` : 'Ver somente urgentes'}
                  </button>
                  <Button type="button" onClick={() => token && loadData(token)} disabled={loading}>
                    {loading ? 'Atualizando…' : 'Atualizar'}
                  </Button>
                </div>
              }
            />

            {billingView === 'all' && (
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                <input className={fieldClass} placeholder="Buscar por cliente ou título" value={billingSearch} onChange={(e) => setBillingSearch(e.target.value)} />
                <select className={fieldClass} value={billingStatusFilter} onChange={(e) => setBillingStatusFilter(e.target.value)}>
                  <option value="">Todos os status</option>
                  <option value="pendente">Pendente</option>
                  <option value="paga">Paga</option>
                  <option value="vencida">Vencida</option>
                  <option value="cancelada">Cancelada</option>
                </select>
                <Button type="button" onClick={() => token && loadData(token)} disabled={loading}>Aplicar filtros</Button>
              </div>
            )}

            <div className="mt-4 space-y-2">
              {(() => {
                const today = new Date();
                today.setHours(0, 0, 0, 0);
                const in7 = new Date(today);
                in7.setDate(in7.getDate() + 7);

                const list = billingView === 'alert'
                  ? billings
                      .filter((b) => {
                        if (b.status === 'vencida') return true;
                        if (b.status === 'pendente') {
                          const due = new Date(b.due_date);
                          return due <= in7;
                        }
                        return false;
                      })
                      .sort((a, b) => {
                        if (a.overdue_days > 0 && b.overdue_days <= 0) return -1;
                        if (b.overdue_days > 0 && a.overdue_days <= 0) return 1;
                        if (a.overdue_days > 0 && b.overdue_days > 0) return b.overdue_days - a.overdue_days;
                        return new Date(a.due_date).getTime() - new Date(b.due_date).getTime();
                      })
                  : billings;

                if (list.length === 0) {
                  return (
                    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-slate-200 py-8 text-center dark:border-slate-700">
                      <CheckCircle2 className="h-8 w-8 text-emerald-400" />
                      <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">
                        {billingView === 'alert' ? 'Nenhuma cobrança urgente no momento.' : 'Nenhuma cobrança encontrada.'}
                      </p>
                      <p className="text-xs text-slate-400">
                        {billingView === 'alert' ? 'Todas as cobranças estão em dia ou fora do período de atenção.' : 'Tente ajustar os filtros.'}
                      </p>
                    </div>
                  );
                }

                return list.map((billing) => {
                  const isOverdue = billing.overdue_days > 0;
                  const isDuesSoon = billing.status === 'pendente' && !isOverdue;
                  return (
                    <div
                      key={billing.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedBilling(billing)}
                      onKeyDown={(e) => cardKeyHandler(e, () => setSelectedBilling(billing))}
                      className={`cursor-pointer rounded-xl border p-4 text-left transition ${
                        selectedBilling?.id === billing.id
                          ? 'border-brand-500 bg-brand-50/60 dark:border-brand-400 dark:bg-brand-900/20'
                          : isOverdue
                          ? 'border-rose-200 bg-rose-50/60 hover:border-rose-300 dark:border-rose-900/60 dark:bg-rose-950/20'
                          : isDuesSoon
                          ? 'border-amber-200 bg-amber-50/60 hover:border-amber-300 dark:border-amber-900/60 dark:bg-amber-950/20'
                          : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-semibold text-slate-900 dark:text-white">{billing.client_name || 'Cliente'}</p>
                            <span className={`rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${statusBadge(billing.status)}`}>
                              {billing.status}
                            </span>
                          </div>
                          <p className="mt-1 truncate text-xs text-slate-500">{billing.title || billing.plan_name || billing.billing_type}</p>
                          <p className="mt-1 text-xs text-slate-400">
                            Venc. {billing.due_date}
                            {billing.vehicle_plate ? ` • ${billing.vehicle_plate}` : ''}
                            {billing.installment_number ? ` • Parcela ${billing.installment_number}/${billing.installment_total}` : ''}
                          </p>
                        </div>
                        <div className="shrink-0 text-right">
                          <p className="font-bold text-slate-900 dark:text-white">{formatCurrency(billing.amount)}</p>
                          {isOverdue && (
                            <p className="mt-0.5 text-xs font-semibold text-rose-600 dark:text-rose-400">{billing.overdue_days}d em atraso</p>
                          )}
                          {isDuesSoon && (
                            <p className="mt-0.5 text-xs font-semibold text-amber-600 dark:text-amber-400">Vence em breve</p>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <SectionHeader eyebrow="Resumo operacional" title="Indicadores de gestão" description="Leitura rápida dos cadastros financeiros e itens lançados no cliente." />
            <div className="mt-5 grid gap-3">
              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Planos cadastrados</p><p className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">{plans.length}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{plans.filter((item) => item.active).length} ativos</p></div>
              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Serviços / produtos</p><p className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">{serviceProducts.length}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{serviceProducts.filter((item) => item.active).length} ativos</p></div>
              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Lançamentos adicionais</p><p className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">{chargeItems.length}</p><p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{chargeItems.filter((item) => item.active).length} em aberto/ativos</p></div>
            </div>
          </Card>

          <Card>
            <SectionHeader eyebrow="Cadastros financeiros" title="Planos, serviços e contratos" description="Edite ou exclua rapidamente os registros estruturais do financeiro sem sair da operação." />
            <div className="mt-5 space-y-5">
              <div>
                <div className="mb-3 flex items-center justify-between gap-3"><p className="text-sm font-semibold text-slate-900 dark:text-white">Planos</p><Button type="button" onClick={openCreatePlan}>Adicionar plano</Button></div>
                <div className="space-y-3">
                  {plans.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-4 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhum plano cadastrado.</p> : plans.map((plan) => (
                    <div key={plan.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900 dark:text-white">{plan.name}</p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{intervalLabel(plan.billing_interval_months)} • {formatCurrency(plan.price)} • {plan.active ? 'Ativo' : 'Inativo'}</p>
                          <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{plan.active_contracts} contrato(s) ativo(s)</p>
                        </div>
                        <div className="flex gap-2">
                          <button type="button" onClick={() => openEditPlan(plan)} className="rounded-2xl border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Editar</button>
                          <button type="button" onClick={() => handleDeletePlan(plan)} className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700">Excluir</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-3 flex items-center justify-between gap-3"><p className="text-sm font-semibold text-slate-900 dark:text-white">Serviços e produtos</p><Button type="button" className="bg-brand-700 hover:bg-brand-600 dark:bg-cyan-400 dark:text-slate-950" onClick={openCreateProduct}>Adicionar item</Button></div>
                <div className="space-y-3">
                  {serviceProducts.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-4 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhum serviço/produto cadastrado.</p> : serviceProducts.map((product) => (
                    <div key={product.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900 dark:text-white">{product.name}</p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{product.category} • {formatCurrency(product.default_price)} • {product.active ? 'Ativo' : 'Inativo'}</p>
                        </div>
                        <div className="flex gap-2">
                          <button type="button" onClick={() => openEditProduct(product)} className="rounded-2xl border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Editar</button>
                          <button type="button" onClick={() => handleDeleteProduct(product)} className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700">Excluir</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-3 flex items-center justify-between gap-3"><p className="text-sm font-semibold text-slate-900 dark:text-white">Contratos</p><Button type="button" className="bg-slate-900 hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white" onClick={openCreateContract}>Adicionar contrato</Button></div>
                <div className="space-y-3">
                  {contracts.length === 0 ? <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-4 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Nenhum contrato cadastrado.</p> : contracts.slice(0, 8).map((contract) => (
                    <div key={contract.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-slate-900 dark:text-white">{contract.client_name || 'Cliente'} • {contract.plan_name || 'Sem plano'}</p>
                          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Início {contract.start_date} • Próximo vencimento {contract.next_due_date || '—'} • {contract.status}</p>
                          <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{contract.open_billings} cobrança(s) em aberto</p>
                        </div>
                        <div className="flex gap-2">
                          <button type="button" onClick={() => openEditContract(contract)} className="rounded-2xl border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Editar</button>
                          <button type="button" onClick={() => handleDeleteContract(contract)} className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700">Excluir</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <SectionHeader eyebrow="Detalhes" title={selectedBilling ? (selectedBilling.title || selectedBilling.client_name || 'Cobrança selecionada') : 'Selecione uma cobrança'} description={selectedBilling ? 'Ações rápidas de recebimento, ajuste, cancelamento e emissão de recibo.' : 'Escolha uma cobrança na listagem para ver o painel de ações.'} />
            {selectedBilling ? (
              <div className="mt-5 space-y-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Cliente</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedBilling.client_name || 'Não informado'}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Valor</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{formatCurrency(selectedBilling.amount)}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Vencimento</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedBilling.due_date}</p></div>
                  <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60"><p className="text-xs uppercase tracking-[0.2em] text-slate-400">Status</p><p className="mt-2 font-semibold text-slate-900 dark:text-white">{selectedBilling.status}</p></div>
                </div>
                <div className="flex flex-wrap gap-3">
                  <Button type="button" disabled={!canEdit || processing || selectedBilling.status === 'paga' || selectedBilling.status === 'cancelada'} onClick={() => setReceiveModal(true)}>Registrar pagamento</Button>
                  <button type="button" disabled={!canEdit || processing || selectedBilling.status === 'cancelada'} onClick={() => setAdjustModal(true)} className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300">Ajustar cobrança</button>
                  <button type="button" disabled={!canEdit || processing || selectedBilling.status === 'cancelada'} onClick={handleCancel} className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm font-semibold text-rose-700 disabled:opacity-50">Cancelar</button>
                  {selectedBilling.status === 'paga' && selectedBilling.receipt_number ? <Button type="button" className="bg-slate-900 hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white" onClick={() => { if (!token) return; downloadProtectedFile(`/billings/${selectedBilling.id}/receipt`, token, `recibo-${selectedBilling.receipt_number}.pdf`).catch((err) => setError(parseError(err))); }}>Baixar recibo</Button> : null}
                </div>
              </div>
            ) : null}
          </Card>
        </div>
      </section>

      <Modal open={planModal} onClose={() => { setPlanModal(false); setEditingPlanId(null); setPlanForm(initialPlanForm); setModalError(''); }} title={editingPlanId ? "Editar plano" : "Novo plano"} description="Cadastre planos com periodicidade mensal, trimestral, semestral ou anual." size="lg">
        <form className="space-y-5" onSubmit={submitPlan}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Nome do plano" value={planForm.name} onChange={(e) => setPlanForm((prev) => ({ ...prev, name: e.target.value }))} required />
            <input className={fieldClass} placeholder="Valor base" value={planForm.price} onChange={(e) => setPlanForm((prev) => ({ ...prev, price: e.target.value }))} required />
            <select className={fieldClass} value={planForm.billing_interval_months} onChange={(e) => setPlanForm((prev) => ({ ...prev, billing_interval_months: e.target.value }))}><option value="1">Mensal</option><option value="3">Trimestral</option><option value="6">Semestral</option><option value="12">Anual</option></select>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={planForm.active} onChange={(e) => setPlanForm((prev) => ({ ...prev, active: e.target.checked }))} /> Plano ativo</label>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição do plano" value={planForm.description} onChange={(e) => setPlanForm((prev) => ({ ...prev, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setPlanModal(false)}>Cancelar</button><Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : editingPlanId ? 'Salvar alterações' : 'Cadastrar plano'}</Button></div>
        </form>
      </Modal>

      <Modal open={productModal} onClose={() => { setProductModal(false); setEditingProductId(null); setServiceProductForm(initialProductForm); setModalError(''); }} title={editingProductId ? "Editar serviço / produto" : "Novo serviço / produto"} description="Cadastre taxas, acessórios, sensores e outros itens cobrados do cliente." size="lg">
        <form className="space-y-5" onSubmit={submitProduct}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Nome do item" value={serviceProductForm.name} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, name: e.target.value }))} required />
            <select className={fieldClass} value={serviceProductForm.category} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, category: e.target.value }))}><option value="servico">Serviço</option><option value="produto">Produto</option><option value="taxa">Taxa</option></select>
            <input className={fieldClass} placeholder="Valor padrão" value={serviceProductForm.default_price} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, default_price: e.target.value }))} required />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.active} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, active: e.target.checked }))} /> Item ativo</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.allow_installments} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, allow_installments: e.target.checked }))} /> Permitir parcelamento</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={serviceProductForm.remove_after_payment} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, remove_after_payment: e.target.checked }))} /> Remover após pagamento</label>
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700 md:col-span-2"><input type="checkbox" checked={serviceProductForm.auto_add_on_uninstall} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, auto_add_on_uninstall: e.target.checked }))} /> Utilizar como taxa de desinstalação</label>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição do item" value={serviceProductForm.description} onChange={(e) => setServiceProductForm((prev) => ({ ...prev, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setProductModal(false)}>Cancelar</button><Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : editingProductId ? 'Salvar alterações' : 'Cadastrar item'}</Button></div>
        </form>
      </Modal>

      <Modal open={contractModal} onClose={() => { setContractModal(false); setEditingContractId(null); setContractForm(initialContractForm); setModalError(''); }} title={editingContractId ? "Editar contrato" : "Novo contrato"} description="Vincule o plano ao cliente e, se necessário, a um veículo e rastreador específicos." size="xl">
        <form className="space-y-5" onSubmit={submitContract}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <select className={fieldClass} value={contractForm.client_id} onChange={(e) => setContractForm((prev) => ({ ...prev, client_id: e.target.value, vehicle_id: '', tracker_id: '' }))} required><option value="">Selecione o cliente</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
            <select className={fieldClass} value={contractForm.plan_id} onChange={(e) => setContractForm((prev) => ({ ...prev, plan_id: e.target.value }))} required><option value="">Selecione o plano</option>{plans.filter((item) => item.active || String(item.id) === contractForm.plan_id).map((plan) => <option key={plan.id} value={plan.id}>{plan.name} • {intervalLabel(plan.billing_interval_months)}</option>)}</select>
            <select className={fieldClass} value={contractForm.vehicle_id} onChange={(e) => setContractForm((prev) => ({ ...prev, vehicle_id: e.target.value, tracker_id: '' }))}><option value="">Sem veículo específico</option>{contractVehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.plate} {vehicle.model ? `• ${vehicle.model}` : ''}</option>)}</select>
            <select className={fieldClass} value={contractForm.tracker_id} onChange={(e) => setContractForm((prev) => ({ ...prev, tracker_id: e.target.value }))}><option value="">Sem rastreador específico</option>{contractTrackers.map((tracker) => <option key={tracker.id} value={tracker.id}>{tracker.imei} {tracker.model ? `• ${tracker.model}` : ''}</option>)}</select>
            <input type="date" className={fieldClass} value={contractForm.start_date} onChange={(e) => setContractForm((prev) => ({ ...prev, start_date: e.target.value }))} required />
            <input type="date" className={fieldClass} value={contractForm.end_date} onChange={(e) => setContractForm((prev) => ({ ...prev, end_date: e.target.value }))} />
            <input className={fieldClass} placeholder="Dia de vencimento" value={contractForm.billing_day} onChange={(e) => setContractForm((prev) => ({ ...prev, billing_day: e.target.value.replace(/\D/g, '').slice(0, 2) }))} />
            <select className={fieldClass} value={contractForm.payment_method} onChange={(e) => setContractForm((prev) => ({ ...prev, payment_method: e.target.value }))}><option value="boleto">Boleto</option><option value="pix">Pix</option><option value="cartao">Cartão</option><option value="deposito">Depósito</option></select>
            <input className={fieldClass} placeholder="Ciclos para gerar" value={contractForm.billing_cycles} onChange={(e) => setContractForm((prev) => ({ ...prev, billing_cycles: e.target.value.replace(/\D/g, '').slice(0, 3) }))} />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700"><input type="checkbox" checked={contractForm.auto_generate_billings} onChange={(e) => setContractForm((prev) => ({ ...prev, auto_generate_billings: e.target.checked }))} /> Gerar cobranças automaticamente</label>
            <div className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400 md:col-span-2">Este contrato pode ser geral do cliente ou ficar amarrado a um veículo e rastreador específicos para facilitar a operação e a cobrança.</div>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Observações" value={contractForm.notes} onChange={(e) => setContractForm((prev) => ({ ...prev, notes: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setContractModal(false)}>Cancelar</button><Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : editingContractId ? 'Salvar alterações' : 'Criar contrato'}</Button></div>
        </form>
      </Modal>

      <Modal open={chargeModal} onClose={() => { setChargeModal(false); setModalError(''); }} title="Novo lançamento financeiro" description="Lance serviços, produtos e taxas com opção de parcelamento e remoção após pagamento." size="xl">
        <form className="space-y-5" onSubmit={submitChargeItem}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <select className={fieldClass} value={chargeItemForm.client_id} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, client_id: e.target.value, contract_id: '', vehicle_id: '', tracker_id: '' }))} required><option value="">Selecione o cliente</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.contract_id} onChange={(e) => { const selected = visibleContracts.find((item) => item.id === Number(e.target.value)); setChargeItemForm((prev) => ({ ...prev, contract_id: e.target.value, vehicle_id: selected?.vehicle_id ? String(selected.vehicle_id) : prev.vehicle_id, tracker_id: selected?.tracker_id ? String(selected.tracker_id) : prev.tracker_id })); }}><option value="">Sem contrato</option>{visibleContracts.map((contract) => <option key={contract.id} value={contract.id}>{contract.client_name} • {contract.plan_name || 'Contrato'}{contract.vehicle_plate ? ` • ${contract.vehicle_plate}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.vehicle_id} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, vehicle_id: e.target.value, tracker_id: '' }))}><option value="">Sem veículo</option>{chargeVehicles.map((vehicle) => <option key={vehicle.id} value={vehicle.id}>{vehicle.plate} {vehicle.model ? `• ${vehicle.model}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.tracker_id} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, tracker_id: e.target.value }))}><option value="">Sem rastreador</option>{chargeTrackers.map((tracker) => <option key={tracker.id} value={tracker.id}>{tracker.imei} {tracker.model ? `• ${tracker.model}` : ''}</option>)}</select>
            <select className={fieldClass} value={chargeItemForm.service_product_id} onChange={(e) => { const selected = serviceProducts.find((item) => item.id === Number(e.target.value)); setChargeItemForm((prev) => ({ ...prev, service_product_id: e.target.value, title: selected?.name || prev.title, unit_price: selected ? String(selected.default_price) : prev.unit_price, remove_after_payment: selected?.remove_after_payment || false })); }}><option value="">Selecione um serviço/produto</option>{serviceProducts.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
            <input className={fieldClass} placeholder="Título da cobrança" value={chargeItemForm.title} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, title: e.target.value }))} required />
            <input className={fieldClass} placeholder="Quantidade" value={chargeItemForm.quantity} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, quantity: e.target.value.replace(/\D/g, '').slice(0, 3) }))} />
            <input className={fieldClass} placeholder="Valor unitário" value={chargeItemForm.unit_price} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, unit_price: e.target.value }))} required />
            <input className={fieldClass} placeholder="Quantidade de parcelas" value={chargeItemForm.installment_count} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, installment_count: e.target.value.replace(/\D/g, '').slice(0, 3) }))} />
            <input type="date" className={fieldClass} value={chargeItemForm.start_date} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, start_date: e.target.value }))} />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm dark:border-slate-700 md:col-span-2"><input type="checkbox" checked={chargeItemForm.remove_after_payment} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, remove_after_payment: e.target.checked }))} /> Remover item após pagamento total das parcelas</label>
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Descrição" value={chargeItemForm.description} onChange={(e) => setChargeItemForm((prev) => ({ ...prev, description: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setChargeModal(false)}>Cancelar</button><Button type="submit" disabled={!canEdit || processing}>{processing ? 'Salvando...' : 'Criar lançamento'}</Button></div>
        </form>
      </Modal>

      <Modal open={receiveModal} onClose={() => { setReceiveModal(false); setModalError(''); }} title="Registrar pagamento" description="Confirme o recebimento da cobrança selecionada e gere o recibo em PDF após a baixa." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Valor pago" value={receiveForm.paid_amount} onChange={(e) => setReceiveForm((prev) => ({ ...prev, paid_amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={receiveForm.payment_date} onChange={(e) => setReceiveForm((prev) => ({ ...prev, payment_date: e.target.value }))} />
            <select className={fieldClass} value={receiveForm.payment_method} onChange={(e) => setReceiveForm((prev) => ({ ...prev, payment_method: e.target.value }))}><option value="pix">Pix</option><option value="boleto">Boleto</option><option value="cartao">Cartão</option><option value="deposito">Depósito</option><option value="dinheiro">Dinheiro</option></select>
            <textarea className={areaClass} placeholder="Observações" value={receiveForm.notes} onChange={(e) => setReceiveForm((prev) => ({ ...prev, notes: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setReceiveModal(false)}>Cancelar</button><Button type="button" disabled={!canEdit || processing} onClick={handleReceive}>{processing ? 'Processando...' : 'Confirmar pagamento'}</Button></div>
        </div>
      </Modal>

      <Modal open={adjustModal} onClose={() => { setAdjustModal(false); setModalError(''); }} title="Ajustar cobrança" description="Atualize valor ou vencimento com justificativa obrigatória para manter rastreabilidade." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <input className={fieldClass} placeholder="Novo valor" value={adjustForm.amount} onChange={(e) => setAdjustForm((prev) => ({ ...prev, amount: e.target.value }))} />
            <input type="date" className={fieldClass} value={adjustForm.due_date} onChange={(e) => setAdjustForm((prev) => ({ ...prev, due_date: e.target.value }))} />
            <textarea className={`${areaClass} md:col-span-2`} placeholder="Justificativa" value={adjustForm.justification} onChange={(e) => setAdjustForm((prev) => ({ ...prev, justification: e.target.value }))} />
          </div>
          <div className="flex justify-end gap-3"><button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setAdjustModal(false)}>Cancelar</button><Button type="button" disabled={!canEdit || processing} onClick={handleAdjust}>{processing ? 'Salvando...' : 'Salvar ajuste'}</Button></div>
        </div>
      </Modal>
    </PageShell>
  );
}
