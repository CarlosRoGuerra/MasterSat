'use client';

import { useState, useEffect, useMemo } from 'react';
import { Check, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { TrackerAutocomplete } from '@/components/ui/tracker-autocomplete';
import { BillingDayInput } from '@/components/ui/billing-day-input';
import { CarneTrackingModal, useCarneTracking } from '@/components/carne-tracking-modal';
import { apiFetch } from '@/lib/api';
import { fetchAddressByCep } from '@/lib/cep';
import { formatZipCode, intervalLabel, onlyDigits, pricePeriodSuffix } from '@/lib/format';

/* ── Types ──────────────────────────────────────────────────────────── */
type ClientOption = {
  id: number;
  name: string;
  cpf_cnpj: string;
  billing_day?: number | null;
  address_zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
};

type TrackerOption = {
  id: number;
  imei: string;
  brand?: string | null;
  model?: string | null;
  status: string;
};

type PlanOption = {
  id: number;
  name: string;
  price: number;
  /** Intervalo de cobrança em meses (1=mensal, 3=trimestral, 6=semestral, 12=anual).
   *  Este é o nome do campo na API; o wizard lia `periodicity`, que não existe —
   *  o valor vinha sempre undefined e TODO plano era tratado como mensal. */
  billing_interval_months?: number;
};


type ServiceProductOption = {
  id: number;
  name: string;
  default_price: number;
};

type DocEntry = {
  category: string;
  files: File[];
};

type VehicleStatus =
  | 'ativo' | 'sem_rastreador' | 'retirado' | 'bloqueado'
  | 'pendente_validacao' | 'em_analise' | 'aprovado' | 'reprovado' | 'correcao_solicitada';

type VehicleFormState = {
  client_id: string;
  status: VehicleStatus;
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
};

/* ── Constants ──────────────────────────────────────────────────────── */
const INITIAL_VF: VehicleFormState = {
  client_id: '', status: 'ativo', plate: '', chassis: '', renavam: '',
  brand: '', model: '', manufacture_year: '', model_year: '',
  color: '', fuel_type: '', type: '',
  sales_point: 'MASTERSAT RASTREAMENTO', seller_consultant: '',
  vehicle_classification: 'NAO INFORMADO', user_alert: 'Nenhum',
  contract_number: '', contract_date: '', contract_end_date: '',
  address_zip_code: '', address_line: '', address_number: '',
  address_complement: '', neighborhood: '', city: '', state: '',
};

const fieldClass =
  'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition ' +
  'placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 ' +
  'dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';

const typeOpts = [
  'Automóvel', 'Ambulância', 'Avião', 'Barco', 'Basculante', 'Bau', 'Bi-trem', 'Bicicleta', 'Caixa',
  'Caminhonete', 'Caminhão', 'Caminhão Bomba de Concreto', 'Caminhão MUK', 'Caminhão de lixo', 'Caminhão tanque',
  'Carregadeira de esteira', 'Carreta', 'Carreta Porta Container', 'Cavalo Mecânico', 'Container', 'Empilhadeira',
  'Equipamento móvel', 'Escavadeira', 'Escavadeira de esteira', 'Gerador', 'Graneleiro', 'Guincho',
  'Guindaste Móvel', 'Helicóptero', 'Jet', 'Lancha', 'Micro ônibus', 'Moto', 'Moto - Viatura', 'Máquina',
  'Máquina Esteira', 'Patinete', 'Pessoa', 'Pet', 'Plataforma', 'Prancha', 'Pá carregadeira', 'Quadriciclo',
  'Retroescavadeira', 'Rolo Compactador', 'Trator', 'Trator de esteira', 'Van', 'Viatura', 'Vírus de carga',
  'ônibus', 'outros',
];
const fuelOpts = ['gasolina', 'etanol', 'flex', 'diesel', 'gnv', 'eletrico', 'hibrido', 'outro'];
const colorOpts = ['preto', 'branco', 'prata', 'cinza', 'azul', 'vermelho', 'verde', 'amarelo', 'marrom', 'bege', 'outro'];
const classifOpts = ['NAO INFORMADO', 'LEVE', 'UTILITARIO', 'PESADO'];
const alertOpts = ['Nenhum', 'Alerta de inadimplência', 'Pendência documental', 'Atenção operacional'];
const docCatOpts = ['crlv', 'documento_veiculo', 'foto_frontal', 'foto_lateral', 'foto_traseira', 'comprovante_propriedade', 'outro'];

const PAYMENT_OPTS = [
  { value: 'boleto', label: 'Boleto', desc: 'Cada parcela gera um boleto individual' },
  { value: 'cartao', label: 'Cartão', desc: 'Cobrado no cartão de crédito' },
  { value: 'pix', label: 'PIX', desc: 'Pagamento via chave PIX' },
  { value: 'dinheiro', label: 'Dinheiro', desc: 'Pagamento em espécie' },
];

const STEPS = [
  { n: 1 as const, label: 'Veículo' },
  { n: 2 as const, label: 'Equipamento' },
  { n: 3 as const, label: 'Plano' },
];

const STEP_TITLES = [
  'Dados do veículo',
  'Vincular equipamento de rastreamento',
  'Selecionar plano e forma de pagamento',
];

function fmt(v: string) { return v.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7); }
function fmtRenavam(v: string) { return v.replace(/\D/g, '').slice(0, 11); }
function fmtChassis(v: string) { return v.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17); }

/* ── Props ──────────────────────────────────────────────────────────── */
interface Props {
  open: boolean;
  token: string;
  clients: ClientOption[];
  onComplete: () => void;
  onClose: () => void;
}

/* ── Wizard ─────────────────────────────────────────────────────────── */
export function VehicleOnboardingWizard({ open, token, clients, onComplete, onClose }: Props) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // Remote data
  const [plans, setPlans] = useState<PlanOption[]>([]);
  const [stockTrackers, setStockTrackers] = useState<TrackerOption[]>([]);
  const [serviceProducts, setServiceProducts] = useState<ServiceProductOption[]>([]);

  // Step 1 — vehicle form
  const [vf, setVf] = useState<VehicleFormState>(INITIAL_VF);
  const [docEntries, setDocEntries] = useState<DocEntry[]>([{ category: 'crlv', files: [] }]);
  const [cepLoading, setCepLoading] = useState(false);

  // Persisted after step 1
  const [vehicle, setVehicle] = useState<{ id: number; plate: string; brand?: string | null; model?: string | null } | null>(null);

  // Step 2 — tracker
  const [tf, setTf] = useState({ tracker_id: '', start_date: new Date().toISOString().slice(0, 10) });
  const [trackerImei, setTrackerImei] = useState('');

  // Step 3 — plan + billing
  const [pf, setPf] = useState({ plan_id: '', payment_method: 'boleto', billing_day: '', billing_mode: 'recorrente' as 'recorrente' | 'carne', num_parcelas: '12' });
  const [selectedProductIds, setSelectedProductIds] = useState<number[]>([]);

  // Ao escolher pagamento em carnê, a confirmação abre o acompanhamento de
  // registro na Ailos (mesma tela usada em Financeiro) em vez de só criar as
  // parcelas locais e fechar o assistente. carneIniciado marca que entramos
  // nesse fluxo, para o efeito abaixo saber que o fechamento do
  // acompanhamento (não o carregamento inicial) é que deve concluir o
  // assistente.
  const carne = useCarneTracking(token);
  const [carneIniciado, setCarneIniciado] = useState(false);

  useEffect(() => {
    if (carneIniciado && !carne.track) {
      setCarneIniciado(false);
      onComplete();
    }
  }, [carne.track, carneIniciado]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load remote data once on open
  useEffect(() => {
    if (!open || !token) return;
    Promise.all([
      apiFetch<PlanOption[]>('/plans', {}, token).catch(() => []),
      apiFetch<TrackerOption[]>('/trackers?status=em_estoque&limit=200', {}, token).catch(() => []),
      apiFetch<ServiceProductOption[]>('/service-products', {}, token).catch(() => []),
    ]).then(([p, t, s]) => { setPlans(p); setStockTrackers(t); setServiceProducts(s); });
  }, [open, token]);

  // Reset on close
  useEffect(() => {
    if (!open) {
      setStep(1); setError(''); setSaving(false);
      setVf(INITIAL_VF); setDocEntries([{ category: 'crlv', files: [] }]);
      setVehicle(null);
      setTf({ tracker_id: '', start_date: new Date().toISOString().slice(0, 10) });
      setTrackerImei('');
      setPf({ plan_id: '', payment_method: 'boleto', billing_day: '', billing_mode: 'recorrente', num_parcelas: '12' });
      setSelectedProductIds([]);
    }
  }, [open]);

  /* ── Billing logic ── */
  const selectedPlan = plans.find(p => String(p.id) === pf.plan_id);
  // Intervalo do plano selecionado; 1 (mensal) como padrão conservador.
  const planIntervalMonths = selectedPlan?.billing_interval_months || 1;
  const isMonthlyPlan = planIntervalMonths === 1;

  const nextBillingDate = (() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth() + 1, 1)
      .toLocaleDateString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
  })();

  const totalProductsFee = selectedProductIds.reduce((sum, pid) => {
    const p = serviceProducts.find(sp => sp.id === pid);
    return sum + (p?.default_price ?? 0);
  }, 0);

  const invoicePreview = useMemo(() => {
    if (!selectedPlan || !tf.start_date) return null;
    const d = new Date(tf.start_date + 'T12:00:00');
    const day = d.getDate();
    if (day === 1) return null;
    const daysInMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    const remaining = daysInMonth - day + 1;
    const prorated = (selectedPlan.price / daysInMonth) * remaining;
    return { prorated, total: prorated + totalProductsFee, remaining, daysInMonth };
  }, [selectedPlan, tf.start_date, totalProductsFee]);

  /* ── Step 1: save vehicle ── */
  async function saveVehicle() {
    if (!vf.client_id || !vf.plate) { setError('Informe o cliente e a placa do veículo.'); return; }
    setSaving(true); setError('');
    try {
      const saved = await apiFetch<{ id: number; plate: string; brand?: string | null; model?: string | null }>(
        '/vehicles',
        {
          method: 'POST',
          body: JSON.stringify({
            client_id: Number(vf.client_id),
            status: vf.status,
            plate: fmt(vf.plate),
            chassis: fmtChassis(vf.chassis) || null,
            renavam: fmtRenavam(vf.renavam) || null,
            brand: vf.brand || null,
            model: vf.model || null,
            manufacture_year: vf.manufacture_year ? Number(vf.manufacture_year) : null,
            model_year: vf.model_year ? Number(vf.model_year) : null,
            color: vf.color || null,
            fuel_type: vf.fuel_type || null,
            type: vf.type || null,
            sales_point: vf.sales_point || null,
            seller_consultant: vf.seller_consultant || null,
            vehicle_classification: vf.vehicle_classification || null,
            user_alert: vf.user_alert || null,
            contract_number: vf.contract_number || null,
            contract_date: vf.contract_date || null,
            contract_end_date: vf.contract_end_date || null,
            address_zip_code: onlyDigits(vf.address_zip_code) || null,
            address_line: vf.address_line || null,
            address_number: vf.address_number || null,
            address_complement: vf.address_complement || null,
            neighborhood: vf.neighborhood || null,
            city: vf.city || null,
            state: vf.state || null,
          }),
        },
        token,
      );

      const docEntriesWithFiles = docEntries.filter(entry => entry.files.length > 0);
      if (docEntriesWithFiles.length) {
        await Promise.all(docEntriesWithFiles.map(entry => {
          const body = new FormData();
          body.append('category', entry.category);
          entry.files.forEach(f => body.append('files', f));
          return apiFetch(`/vehicles/${saved.id}/documents`, { method: 'POST', body }, token);
        }));
      }

      const client = clients.find(c => c.id === Number(vf.client_id));
      if (client?.billing_day) setPf(prev => ({ ...prev, billing_day: String(client.billing_day) }));

      setVehicle(saved);
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao cadastrar veículo.');
    } finally { setSaving(false); }
  }

  /* ── Step 2: link tracker ── */
  async function linkTracker() {
    if (!vehicle || !tf.tracker_id) { setError('Selecione um rastreador.'); return; }
    setSaving(true); setError('');
    try {
      await apiFetch(
        `/trackers/${tf.tracker_id}/link-vehicle`,
        {
          method: 'POST',
          body: JSON.stringify({
            vehicle_id: vehicle.id,
            start_date: tf.start_date,
          }),
        },
        token,
      );
      const tracker = stockTrackers.find(t => String(t.id) === tf.tracker_id);
      setTrackerImei(tracker?.imei ?? '');
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao vincular equipamento.');
    } finally { setSaving(false); }
  }

  /* ── Step 3: confirm plan ── */
  async function confirmPlan() {
    if (!vehicle || !pf.plan_id) { setError('Selecione um plano.'); return; }
    const carneMode = pf.payment_method === 'boleto' && pf.billing_mode === 'carne';
    const numParcelas = Number(pf.num_parcelas);
    if (carneMode && (!numParcelas || numParcelas < 2)) {
      setError('Informe ao menos 2 parcelas para o carnê.');
      return;
    }
    setSaving(true); setError('');
    try {
      // Create contract
      const contract = await apiFetch<{ id: number }>(
        '/contracts',
        {
          method: 'POST',
          body: JSON.stringify({
            client_id: Number(vf.client_id),
            plan_id: Number(pf.plan_id),
            vehicle_id: vehicle.id,
            tracker_id: tf.tracker_id ? Number(tf.tracker_id) : null,
            start_date: tf.start_date,
            billing_day: pf.billing_day ? Number(pf.billing_day) : null,
            payment_method: pf.payment_method,
          }),
        },
        token,
      );

      // Carnê: cria as N parcelas já de uma vez (o fechamento mensal reconhece
      // essas parcelas pelo period_label e não gera mensalidade em cima delas).
      let parcelasCarne: { id: number }[] = [];
      if (carneMode) {
        parcelasCarne = await apiFetch<{ id: number }[]>(
          '/billings/parcelar',
          {
            method: 'POST',
            body: JSON.stringify({
              contract_id: contract.id,
              num_parcelas: numParcelas,
            }),
          },
          token,
        );
      }

      // Create a charge item for each selected service product
      await Promise.all(
        selectedProductIds.map(pid => {
          const product = serviceProducts.find(sp => sp.id === pid);
          if (!product) return Promise.resolve();
          return apiFetch(
            '/client-charge-items',
            {
              method: 'POST',
              body: JSON.stringify({
                client_id: Number(vf.client_id),
                contract_id: contract.id,
                vehicle_id: vehicle.id,
                service_product_id: pid,
                title: product.name,
                quantity: 1,
                unit_price: product.default_price,
                installment_count: 1,
                start_date: tf.start_date,
                remove_after_payment: true,
              }),
            },
            token,
          );
        }),
      );

      // Carnê: abre o acompanhamento de registro na Ailos em vez de fechar o
      // assistente na hora — sem isso, as parcelas ficavam criadas mas sem
      // boleto real gerado até alguém lembrar de ir em Financeiro fazer isso
      // manualmente. onComplete() só é chamado quando o acompanhamento fechar
      // (ver o efeito que observa carne.track).
      if (carneMode && parcelasCarne.length > 0) {
        setCarneIniciado(true);
        setSaving(false);
        await carne.iniciar(parcelasCarne.map(b => b.id));
        return;
      }

      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao confirmar contratação.');
      setSaving(false);
      return;
    }
    setSaving(false);
  }

  /* ── CEP lookup ── */
  async function lookupCep(value: string) {
    const digits = onlyDigits(value);
    if (digits.length !== 8) return;
    setCepLoading(true);
    try {
      const addr = await fetchAddressByCep(digits);
      if (addr) {
        setVf(prev => ({
          ...prev,
          address_zip_code: formatZipCode(digits),
          address_line: addr.address_line || prev.address_line,
          neighborhood: addr.neighborhood || prev.neighborhood,
          city: addr.city || prev.city,
          state: addr.state || prev.state,
        }));
      }
    } catch { /* silent */ }
    finally { setCepLoading(false); }
  }

  /* ── Document entries ── */
  function addDocEntry() {
    setDocEntries(prev => [...prev, { category: 'outro', files: [] }]);
  }

  function updateDocEntry(index: number, patch: Partial<DocEntry>) {
    setDocEntries(prev => prev.map((entry, i) => i === index ? { ...entry, ...patch } : entry));
  }

  function removeDocEntry(index: number) {
    setDocEntries(prev => prev.filter((_, i) => i !== index));
  }

  function fillFromClient() {
    const c = clients.find(cl => cl.id === Number(vf.client_id));
    if (!c) return;
    setVf(prev => ({
      ...prev,
      address_zip_code: c.address_zip_code ? formatZipCode(c.address_zip_code) : prev.address_zip_code,
      address_line: c.address_line || prev.address_line,
      address_number: c.address_number || prev.address_number,
      address_complement: c.address_complement || prev.address_complement,
      neighborhood: c.neighborhood || prev.neighborhood,
      city: c.city || prev.city,
      state: c.state || prev.state,
    }));
  }

  if (!open) return null;

  // Não fecha o assistente enquanto o carnê está sendo registrado/acompanhado
  // na Ailos — fechar aqui derrubaria o acompanhamento em voo (fica embutido
  // no ciclo de vida deste componente, controlado pelo "open" do pai).
  function handleClose() {
    if (carne.track) return;
    onClose();
  }

  const selectedTracker = stockTrackers.find(t => String(t.id) === tf.tracker_id);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 py-8 backdrop-blur-sm">
      <div className="w-full max-w-3xl rounded-2xl bg-white shadow-2xl dark:bg-slate-900">

        {/* ── Header: stepper + context anchor ── */}
        <div className="sticky top-0 z-10 rounded-t-2xl border-b border-slate-100 bg-white px-6 py-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                Novo veículo — etapa {step} de 3
              </p>
              <h2 className="mt-0.5 text-lg font-bold text-slate-900 dark:text-white">
                {STEP_TITLES[step - 1]}
              </h2>
            </div>
            <button
              onClick={handleClose}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 text-slate-400 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Stepper progress bar */}
          <div className="mt-5 flex items-center">
            {STEPS.map((s, i) => (
              <div key={s.n} className="flex flex-1 items-center">
                <div className="flex flex-col items-center gap-1.5">
                  <div
                    className={[
                      'flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold transition-all duration-300',
                      step > s.n
                        ? 'bg-emerald-500 text-white'
                        : step === s.n
                        ? 'bg-brand-700 text-white ring-4 ring-brand-200 dark:ring-brand-900/60'
                        : 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500',
                    ].join(' ')}
                  >
                    {step > s.n ? <Check className="h-4 w-4" /> : s.n}
                  </div>
                  <span
                    className={[
                      'whitespace-nowrap text-[11px] font-semibold',
                      step === s.n
                        ? 'text-brand-700 dark:text-brand-300'
                        : step > s.n
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-slate-400 dark:text-slate-500',
                    ].join(' ')}
                  >
                    {s.label}
                  </span>
                </div>
                {i < STEPS.length - 1 && (
                  <div
                    className={[
                      'mx-2 mb-4 h-0.5 flex-1 transition-colors duration-500',
                      step > s.n ? 'bg-emerald-400' : 'bg-slate-200 dark:bg-slate-700',
                    ].join(' ')}
                  />
                )}
              </div>
            ))}
          </div>

          {/* Context anchor: vehicle plate visible from step 2 onwards */}
          {vehicle && step > 1 && (
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 dark:border-emerald-900/40 dark:bg-emerald-950/30">
              <span className="text-sm font-bold text-emerald-700 dark:text-emerald-300">
                {vehicle.plate}
              </span>
              {(vehicle.brand || vehicle.model) && (
                <span className="text-sm text-emerald-600 dark:text-emerald-400">
                  {[vehicle.brand, vehicle.model].filter(Boolean).join(' ')}
                </span>
              )}
              {trackerImei && step === 3 && (
                <>
                  <span className="text-emerald-300 dark:text-emerald-700">·</span>
                  <span className="font-mono text-sm text-emerald-600 dark:text-emerald-400">{trackerImei}</span>
                </>
              )}
            </div>
          )}
        </div>

        {/* ── Body ── */}
        <div className="p-6">
          {error && (
            <p className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
              {error}
            </p>
          )}

          {/* ════════════════════════════════════════════════
              STEP 1 — Vehicle form
          ════════════════════════════════════════════════ */}
          {step === 1 && (
            <div className="space-y-6">
              {/* Client + status */}
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">
                    Cliente <span className="text-red-500">*</span>
                  </label>
                  <ClientAutocomplete
                    clients={clients}
                    value={vf.client_id}
                    onChange={id => setVf(prev => ({ ...prev, client_id: id }))}
                    placeholder="Digite nome ou CPF/CNPJ…"
                    required
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Status</label>
                  <select
                    className={fieldClass}
                    value={vf.status}
                    onChange={e => setVf(prev => ({ ...prev, status: e.target.value as VehicleStatus }))}
                  >
                    {(['ativo','sem_rastreador','retirado','bloqueado','pendente_validacao','em_analise','aprovado','reprovado','correcao_solicitada'] as const).map(s => (
                      <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="border-t border-slate-100 dark:border-slate-800" />

              {/* Vehicle identification */}
              <div>
                <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                  Identificação do veículo
                </p>
                <div className="grid gap-4 md:grid-cols-3">
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">
                      Placa <span className="text-red-500">*</span>
                    </label>
                    <input
                      className={fieldClass}
                      placeholder="ABC1D23"
                      value={vf.plate}
                      onChange={e => setVf(prev => ({ ...prev, plate: fmt(e.target.value) }))}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Chassi</label>
                    <input
                      className={fieldClass}
                      placeholder="17 caracteres"
                      value={vf.chassis}
                      onChange={e => setVf(prev => ({ ...prev, chassis: fmtChassis(e.target.value) }))}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Renavam</label>
                    <input
                      className={fieldClass}
                      placeholder="11 dígitos"
                      value={vf.renavam}
                      onChange={e => setVf(prev => ({ ...prev, renavam: fmtRenavam(e.target.value) }))}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Marca</label>
                    <input
                      className={fieldClass}
                      placeholder="Ex.: Toyota"
                      value={vf.brand}
                      onChange={e => setVf(prev => ({ ...prev, brand: e.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Modelo</label>
                    <input
                      className={fieldClass}
                      placeholder="Ex.: Corolla"
                      value={vf.model}
                      onChange={e => setVf(prev => ({ ...prev, model: e.target.value }))}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Fab.</label>
                      <input
                        className={fieldClass}
                        placeholder="2023"
                        value={vf.manufacture_year}
                        onChange={e => setVf(prev => ({ ...prev, manufacture_year: onlyDigits(e.target.value).slice(0, 4) }))}
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Mod.</label>
                      <input
                        className={fieldClass}
                        placeholder="2024"
                        value={vf.model_year}
                        onChange={e => setVf(prev => ({ ...prev, model_year: onlyDigits(e.target.value).slice(0, 4) }))}
                      />
                    </div>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Tipo</label>
                    <select className={fieldClass} value={vf.type} onChange={e => setVf(prev => ({ ...prev, type: e.target.value }))}>
                      <option value="">Selecione</option>
                      {typeOpts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Combustível</label>
                    <select className={fieldClass} value={vf.fuel_type} onChange={e => setVf(prev => ({ ...prev, fuel_type: e.target.value }))}>
                      <option value="">Selecione</option>
                      {fuelOpts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Cor</label>
                    <select className={fieldClass} value={vf.color} onChange={e => setVf(prev => ({ ...prev, color: e.target.value }))}>
                      <option value="">Selecione</option>
                      {colorOpts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Classificação</label>
                    <select className={fieldClass} value={vf.vehicle_classification} onChange={e => setVf(prev => ({ ...prev, vehicle_classification: e.target.value }))}>
                      {classifOpts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Alerta de usuário</label>
                    <select className={fieldClass} value={vf.user_alert} onChange={e => setVf(prev => ({ ...prev, user_alert: e.target.value }))}>
                      {alertOpts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Consultor/responsável</label>
                    <input
                      className={fieldClass}
                      placeholder="Nome do consultor"
                      value={vf.seller_consultant}
                      onChange={e => setVf(prev => ({ ...prev, seller_consultant: e.target.value }))}
                    />
                  </div>
                </div>
              </div>

              <div className="border-t border-slate-100 dark:border-slate-800" />

              {/* Address */}
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                    Endereço
                  </p>
                  {vf.client_id && (
                    <button
                      type="button"
                      onClick={fillFromClient}
                      className="text-xs text-brand-600 hover:underline dark:text-brand-400"
                    >
                      Usar endereço do cliente
                    </button>
                  )}
                </div>
                <div className="grid gap-4 md:grid-cols-4">
                  <div className="flex items-start gap-2">
                    <input
                      className={fieldClass}
                      placeholder="CEP"
                      value={vf.address_zip_code}
                      onChange={e => setVf(prev => ({ ...prev, address_zip_code: formatZipCode(e.target.value) }))}
                      onBlur={e => lookupCep(e.target.value)}
                    />
                    {cepLoading && (
                      <span className="mt-2.5 text-xs text-slate-400">…</span>
                    )}
                  </div>
                  <input
                    className="col-span-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                    placeholder="Logradouro"
                    value={vf.address_line}
                    onChange={e => setVf(prev => ({ ...prev, address_line: e.target.value }))}
                  />
                  <input className={fieldClass} placeholder="Número" value={vf.address_number} onChange={e => setVf(prev => ({ ...prev, address_number: e.target.value }))} />
                  <input className={fieldClass} placeholder="Complemento" value={vf.address_complement} onChange={e => setVf(prev => ({ ...prev, address_complement: e.target.value }))} />
                  <input className={fieldClass} placeholder="Bairro" value={vf.neighborhood} onChange={e => setVf(prev => ({ ...prev, neighborhood: e.target.value }))} />
                  <input className={fieldClass} placeholder="Cidade" value={vf.city} onChange={e => setVf(prev => ({ ...prev, city: e.target.value }))} />
                  <input
                    className={fieldClass}
                    placeholder="UF"
                    value={vf.state}
                    maxLength={2}
                    onChange={e => setVf(prev => ({ ...prev, state: e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))}
                  />
                </div>
              </div>

              {/* Documentation */}
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900/50">
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Documentação inicial</p>
                <p className="mt-0.5 text-xs text-slate-400">Opcional. Envie agora ou anexe depois na aba de documentos.</p>
                <div className="mt-3 space-y-3">
                  {docEntries.map((entry, idx) => (
                    <div key={idx} className="grid gap-3 sm:grid-cols-[200px_1fr_auto]">
                      <select
                        className={fieldClass}
                        value={entry.category}
                        onChange={e => updateDocEntry(idx, { category: e.target.value })}
                      >
                        {docCatOpts.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                      <input
                        type="file"
                        multiple
                        className={`${fieldClass} file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:text-white`}
                        onChange={e => updateDocEntry(idx, { files: Array.from(e.target.files || []) })}
                      />
                      {docEntries.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeDocEntry(idx)}
                          className="flex h-10 w-10 shrink-0 items-center justify-center justify-self-start rounded-xl border border-slate-200 text-slate-400 hover:bg-slate-100 hover:text-red-500 dark:border-slate-700 dark:hover:bg-slate-800"
                          aria-label="Remover documento"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addDocEntry}
                  className="mt-3 text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400"
                >
                  + Adicionar outro documento
                </button>
              </div>

              {/* Actions */}
              <div className="flex justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
                <Button variant="secondary" onClick={onClose}>Cancelar</Button>
                <Button onClick={saveVehicle} disabled={saving || !vf.client_id || !vf.plate}>
                  {saving ? 'Salvando…' : 'Cadastrar e avançar →'}
                </Button>
              </div>
            </div>
          )}

          {/* ════════════════════════════════════════════════
              STEP 2 — Tracker linking
          ════════════════════════════════════════════════ */}
          {step === 2 && (
            <div className="space-y-6">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Selecione o equipamento de rastreamento disponível em estoque para instalar neste veículo.
              </p>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">
                    Equipamento em estoque <span className="text-red-500">*</span>
                  </label>
                  {stockTrackers.length === 0 ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">
                      Nenhum rastreador em estoque disponível.
                    </div>
                  ) : (
                    <TrackerAutocomplete
                      trackers={stockTrackers}
                      value={tf.tracker_id}
                      onChange={id => setTf(prev => ({ ...prev, tracker_id: id }))}
                    />
                  )}
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">
                    Data de instalação
                  </label>
                  <input
                    className={fieldClass}
                    type="date"
                    value={tf.start_date}
                    onChange={e => setTf(prev => ({ ...prev, start_date: e.target.value }))}
                  />
                </div>
              </div>

              {/* Selected tracker summary card */}
              {selectedTracker && (
                <div className="rounded-xl border border-brand-200 bg-brand-50/70 p-4 dark:border-brand-800/40 dark:bg-brand-950/30">
                  <p className="mb-2 text-xs font-bold uppercase tracking-widest text-brand-600 dark:text-brand-400">
                    Equipamento selecionado
                  </p>
                  <p className="font-mono text-base font-semibold text-slate-900 dark:text-white">
                    {selectedTracker.imei}
                  </p>
                  {(selectedTracker.brand || selectedTracker.model) && (
                    <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                      {[selectedTracker.brand, selectedTracker.model].filter(Boolean).join(' ')}
                    </p>
                  )}
                </div>
              )}

              <div className="flex justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
                <Button variant="secondary" onClick={onComplete}>
                  Concluir sem rastreador
                </Button>
                <Button onClick={linkTracker} disabled={saving || !tf.tracker_id}>
                  {saving ? 'Vinculando…' : 'Vincular e avançar →'}
                </Button>
              </div>
            </div>
          )}

          {/* ════════════════════════════════════════════════
              STEP 3 — Plan + Billing
          ════════════════════════════════════════════════ */}
          {step === 3 && (
            <div className="space-y-6">

              {/* Plan selection */}
              <div>
                <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                  Plano de rastreamento
                </p>
                {plans.length === 0 ? (
                  <p className="text-sm text-slate-400">Nenhum plano cadastrado no sistema.</p>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {/* "No plan" option */}
                    <button
                      type="button"
                      onClick={() => setPf(prev => ({ ...prev, plan_id: '' }))}
                      className={[
                        'rounded-xl border px-4 py-3 text-left text-sm transition-colors',
                        !pf.plan_id
                          ? 'border-slate-400 bg-slate-100 dark:border-slate-500 dark:bg-slate-800'
                          : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/60',
                      ].join(' ')}
                    >
                      <span className="block font-semibold">Sem plano</span>
                      <span className="mt-0.5 block text-xs text-slate-400">Vincular sem contratação agora</span>
                    </button>

                    {plans.map(plan => (
                      <button
                        key={plan.id}
                        type="button"
                        onClick={() => setPf(prev => ({ ...prev, plan_id: String(plan.id) }))}
                        className={[
                          'rounded-xl border px-4 py-3 text-left text-sm transition-colors',
                          pf.plan_id === String(plan.id)
                            ? 'border-brand-500 bg-brand-50 dark:border-brand-400 dark:bg-brand-950/30'
                            : 'border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800/60',
                        ].join(' ')}
                      >
                        <span className={`block font-semibold ${pf.plan_id === String(plan.id) ? 'text-brand-700 dark:text-brand-300' : ''}`}>
                          {plan.name}
                        </span>
                        <span className="mt-0.5 block font-mono text-sm text-brand-600 dark:text-brand-400">
                          R$ {Number(plan.price).toFixed(2)}{pricePeriodSuffix(plan.billing_interval_months || 1)}
                        </span>
                        {(plan.billing_interval_months || 1) > 1 && (
                          <span className="mt-0.5 block text-xs text-slate-400">
                            {intervalLabel(plan.billing_interval_months || 1)}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Billing section — only when a plan is selected */}
              {pf.plan_id && (
                <>
                  {/* Payment method */}
                  <div>
                    <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                      Forma de pagamento
                    </p>
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                      {PAYMENT_OPTS.map(opt => (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => setPf(prev => ({
                            ...prev,
                            payment_method: opt.value,
                            // Carnê é um mecanismo de boleto na Ailos — não existe em cartão/PIX/dinheiro.
                            billing_mode: opt.value === 'boleto' ? prev.billing_mode : 'recorrente',
                          }))}
                          className={[
                            'rounded-xl border px-3 py-3 text-center text-sm font-semibold transition-colors',
                            pf.payment_method === opt.value
                              ? 'border-brand-500 bg-brand-50 text-brand-700 dark:border-brand-400 dark:bg-brand-950/30 dark:text-brand-300'
                              : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/60',
                          ].join(' ')}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Billing mode: recorrente mensal x carnê (só faz sentido com boleto) */}
                  {pf.payment_method === 'boleto' && (
                    <div>
                      <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                        Modalidade de cobrança
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => setPf(prev => ({ ...prev, billing_mode: 'recorrente' }))}
                          className={[
                            'rounded-xl border px-4 py-3 text-left text-sm transition-colors',
                            pf.billing_mode === 'recorrente'
                              ? 'border-brand-500 bg-brand-50 dark:border-brand-400 dark:bg-brand-950/30'
                              : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/60',
                          ].join(' ')}
                        >
                          <span className="block font-semibold">Recorrente mensal</span>
                          <span className="mt-0.5 block text-xs text-slate-400">Um boleto por mês, gerado no fechamento</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setPf(prev => ({ ...prev, billing_mode: 'carne' }))}
                          className={[
                            'rounded-xl border px-4 py-3 text-left text-sm transition-colors',
                            pf.billing_mode === 'carne'
                              ? 'border-brand-500 bg-brand-50 dark:border-brand-400 dark:bg-brand-950/30'
                              : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/60',
                          ].join(' ')}
                        >
                          <span className="block font-semibold">Carnê</span>
                          <span className="mt-0.5 block text-xs text-slate-400">N parcelas já criadas de uma vez</span>
                        </button>
                      </div>

                      {pf.billing_mode === 'carne' && (
                        <div className="mt-3 flex items-center gap-3">
                          <label className="text-sm">
                            <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Quantidade de parcelas</span>
                            <input
                              className={fieldClass}
                              inputMode="numeric"
                              value={pf.num_parcelas}
                              onChange={e => setPf(prev => ({ ...prev, num_parcelas: e.target.value.replace(/\D/g, '').slice(0, 2) }))}
                              placeholder="12"
                            />
                          </label>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Recorrência: vale para qualquer intervalo. Antes o bloco
                      só aparecia quando isMonthlyPlan — e como isMonthlyPlan
                      era sempre true por causa do campo errado, um plano
                      trimestral/anual era anunciado como mensal. */}
                  {pf.billing_mode === 'recorrente' && (
                    <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 dark:border-blue-900/40 dark:bg-blue-950/30">
                      <p className="text-sm font-semibold text-blue-700 dark:text-blue-300">
                        Cobrança recorrente {intervalLabel(planIntervalMonths).toLowerCase()}
                      </p>
                      <p className="mt-1 text-sm text-blue-600 dark:text-blue-400">
                        {isMonthlyPlan
                          ? 'Processada em lote entre os dias 1º e 2 de cada mês.'
                          : `R$ ${selectedPlan ? Number(selectedPlan.price).toFixed(2) : '—'} a cada ${planIntervalMonths} meses, processada no fechamento do mês de vencimento.`}
                      </p>
                      <p className="mt-1 text-xs text-blue-500 dark:text-blue-500">
                        Estimativa da 1ª cobrança:{' '}
                        <strong className="text-blue-700 dark:text-blue-300">{nextBillingDate}</strong>
                      </p>
                    </div>
                  )}

                  {/* Carnê: parcelas criadas no cadastro; registro na Ailos fica para Financeiro */}
                  {pf.billing_mode === 'carne' && (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900/40 dark:bg-amber-950/30">
                      <p className="text-sm font-semibold text-amber-700 dark:text-amber-300">
                        Carnê de {Number(pf.num_parcelas) || 0} parcela(s)
                      </p>
                      <p className="mt-1 text-sm text-amber-600 dark:text-amber-400">
                        {Number(pf.num_parcelas) || 0} cobranças mensais de {selectedPlan ? `R$ ${Number(selectedPlan.price).toFixed(2)}` : 'valor do plano'} serão criadas junto com o contrato.
                      </p>
                      <p className="mt-1 text-xs text-amber-600 dark:text-amber-500">
                        O registro dos boletos na Ailos e o PDF do carnê ficam em Financeiro → Gerar carnê.
                      </p>
                    </div>
                  )}


                  {/* Billing day */}
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">Dia do vencimento</p>
                        {pf.billing_day ? (
                          <p className="mt-1 text-base font-bold text-slate-900 dark:text-white">
                            Todo dia{' '}
                            <span className="text-brand-700 dark:text-brand-300">{pf.billing_day}</span>
                          </p>
                        ) : (
                          <p className="mt-1 text-sm text-amber-600 dark:text-amber-400">
                            Não configurado no cadastro do cliente
                          </p>
                        )}
                        <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                          Definido no cadastro do cliente · unifica todos os veículos
                        </p>
                      </div>
                      <div className="shrink-0">
                        <BillingDayInput
                          value={pf.billing_day}
                          onChange={v => setPf(prev => ({ ...prev, billing_day: v }))}
                          placeholder="Dia"
                          className="w-24 text-center"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Additional services */}
                  {serviceProducts.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                        Serviços adicionais
                      </p>
                      <p className="mb-3 text-xs text-slate-400 dark:text-slate-500">
                        Cobrados na 1ª fatura junto com a instalação.
                      </p>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {serviceProducts.map(prod => {
                          const checked = selectedProductIds.includes(prod.id);
                          return (
                            <label
                              key={prod.id}
                              className={[
                                'flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition-colors',
                                checked
                                  ? 'border-brand-400 bg-brand-50 dark:border-brand-600 dark:bg-brand-950/30'
                                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/60',
                              ].join(' ')}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() =>
                                  setSelectedProductIds(prev =>
                                    prev.includes(prod.id)
                                      ? prev.filter(x => x !== prod.id)
                                      : [...prev, prod.id],
                                  )
                                }
                                className="h-4 w-4 rounded accent-brand-700"
                              />
                              <div className="min-w-0 flex-1">
                                <p className="truncate font-medium text-slate-800 dark:text-slate-200">{prod.name}</p>
                                <p className="text-xs text-slate-400">R$ {Number(prod.default_price).toFixed(2)}</p>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Invoice preview (pro-rata) */}
                  {invoicePreview && (
                    <div className="rounded-xl border border-brand-200 bg-brand-50/60 p-4 dark:border-brand-800/60 dark:bg-brand-950/30">
                      <p className="mb-2 text-xs font-bold uppercase tracking-widest text-brand-600 dark:text-brand-400">
                        Prévia da 1ª fatura — pró-rata ({invoicePreview.remaining}/{invoicePreview.daysInMonth} dias)
                      </p>
                      <div className="space-y-1 text-sm text-slate-700 dark:text-slate-300">
                        <div className="flex justify-between">
                          <span>Mensalidade proporcional</span>
                          <span className="font-mono">R$ {invoicePreview.prorated.toFixed(2)}</span>
                        </div>
                        {selectedProductIds.map(pid => {
                          const prod = serviceProducts.find(sp => sp.id === pid);
                          if (!prod) return null;
                          return (
                            <div key={pid} className="flex justify-between text-slate-500 dark:text-slate-400">
                              <span>{prod.name}</span>
                              <span className="font-mono">R$ {Number(prod.default_price).toFixed(2)}</span>
                            </div>
                          );
                        })}
                        <div className="mt-2 flex justify-between border-t border-brand-200 pt-2 font-bold dark:border-brand-800/60">
                          <span>Total 1ª fatura</span>
                          <span className="font-mono text-brand-700 dark:text-brand-300">
                            R$ {invoicePreview.total.toFixed(2)}
                          </span>
                        </div>
                      </div>
                      <p className="mt-2 text-xs text-slate-400">
                        {isMonthlyPlan ? 'A partir do mês seguinte' : 'Nas próximas cobranças'}:{' '}
                        R$ {selectedPlan ? Number(selectedPlan.price).toFixed(2) : '—'}
                        {pricePeriodSuffix(planIntervalMonths)}
                      </p>
                    </div>
                  )}
                </>
              )}

              <div className="flex justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
                <Button variant="secondary" onClick={onComplete}>
                  Concluir sem plano
                </Button>
                <Button onClick={confirmPlan} disabled={saving}>
                  {saving ? 'Confirmando…' : 'Confirmar contratação ✓'}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <CarneTrackingModal carne={carne} />
    </div>
  );
}
