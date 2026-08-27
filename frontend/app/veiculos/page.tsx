'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { BillingDayInput } from '@/components/ui/billing-day-input';
import { useDebouncedValue, useEffectSkipFirst } from '@/lib/use-debounced-value';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { Eye, DollarSign, ClipboardList, Pencil, CreditCard, Zap, Building2, Banknote, FileText as FileBillet, Search, X, AlertTriangle } from 'lucide-react';
import { ExportButton } from '@/components/ui/export-button';
import { apiFetch } from '@/lib/api';
import { fetchAddressByCep } from '@/lib/cep';
import { formatZipCode, onlyDigits, pricePeriodSuffix } from '@/lib/format';
import { useAuthGuard } from '@/lib/use-auth-guard';
import { ROUTE_ROLES } from '@/lib/route-roles';
import { VehicleOnboardingWizard } from '@/components/vehicle-onboarding-wizard';
import type { ClientOption, TrackerOption, VehicleStatus } from '@/lib/domain-types';

type ServiceProductOption = { id: number; name: string; default_price: number; auto_add_on_uninstall?: boolean };
type ContractOption = { id: number; client_id: number; plan_name?: string | null; status: string };
type PlanOption = { id: number; name: string; price: number; billing_interval_months?: number };

type Vehicle = {
  id: number;
  client_id: number;
  sales_point?: string | null;
  seller_consultant?: string | null;
  vehicle_classification?: string | null;
  user_alert?: string | null;
  contract_number?: string | null;
  contract_date?: string | null;
  contract_end_date?: string | null;
  address_zip_code?: string | null;
  address_line?: string | null;
  address_number?: string | null;
  address_complement?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  state?: string | null;
  plate: string;
  chassis?: string | null;
  renavam?: string | null;
  brand?: string | null;
  model?: string | null;
  manufacture_year?: number | null;
  model_year?: number | null;
  color?: string | null;
  fuel_type?: string | null;
  type?: string | null;
  status: VehicleStatus;
};

type VehicleDocument = {
  id: number;
  file_name: string;
  category: string;
  review_status?: string;
  review_notes?: string | null;
  url: string;
  download_url: string;
};

type VehicleFormState = {
  client_id: string;
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
  status: VehicleStatus;
};

const initialForm: VehicleFormState = {
  client_id: '',
  sales_point: 'MASTERSAT RASTREAMENTO',
  seller_consultant: '',
  vehicle_classification: 'NAO INFORMADO',
  user_alert: 'Nenhum',
  contract_number: '',
  contract_date: '',
  contract_end_date: '',
  address_zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
  plate: '',
  chassis: '',
  renavam: '',
  brand: '',
  model: '',
  manufacture_year: '',
  model_year: '',
  color: '',
  fuel_type: '',
  type: '',
  status: 'ativo',
};

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';
const areaClass = `${fieldClass} min-h-[88px] resize-y`;
const salesPointOptions = ['MASTERSAT RASTREAMENTO'];
const classificationOptions = ['NAO INFORMADO', 'LEVE', 'UTILITARIO', 'PESADO'];
const alertOptions = ['Nenhum', 'Alerta de inadimplência', 'Pendência documental', 'Atenção operacional'];
const typeOptions = [
  'Automóvel', 'Ambulância', 'Avião', 'Barco', 'Basculante', 'Bau', 'Bi-trem', 'Bicicleta', 'Caixa',
  'Caminhonete', 'Caminhão', 'Caminhão Bomba de Concreto', 'Caminhão MUK', 'Caminhão de lixo', 'Caminhão tanque',
  'Carregadeira de esteira', 'Carreta', 'Carreta Porta Container', 'Cavalo Mecânico', 'Container', 'Empilhadeira',
  'Equipamento móvel', 'Escavadeira', 'Escavadeira de esteira', 'Gerador', 'Graneleiro', 'Guincho',
  'Guindaste Móvel', 'Helicóptero', 'Jet', 'Lancha', 'Micro ônibus', 'Moto', 'Moto - Viatura', 'Máquina',
  'Máquina Esteira', 'Patinete', 'Pessoa', 'Pet', 'Plataforma', 'Prancha', 'Pá carregadeira', 'Quadriciclo',
  'Retroescavadeira', 'Rolo Compactador', 'Trator', 'Trator de esteira', 'Van', 'Viatura', 'Vírus de carga',
  'ônibus', 'outros',
];
const fuelOptions = ['gasolina', 'etanol', 'flex', 'diesel', 'gnv', 'eletrico', 'hibrido', 'outro'];
const colorOptions = ['preto', 'branco', 'prata', 'cinza', 'azul', 'vermelho', 'verde', 'amarelo', 'marrom', 'bege', 'outro'];
const documentCategoryOptions = ['documento_veiculo', 'crlv', 'foto_frontal', 'foto_lateral', 'foto_traseira', 'comprovante_propriedade', 'outro'];

const PAYMENT_METHODS = [
  { value: 'boleto',    label: 'Boleto',      desc: 'Código de barras',      Icon: FileBillet   },
  { value: 'cartao',   label: 'Cartão',       desc: 'Débito recorrente',     Icon: CreditCard   },
  { value: 'pix',      label: 'PIX',          desc: 'Transferência rápida',  Icon: Zap          },
  { value: 'dinheiro', label: 'Dinheiro',     desc: 'Pagamento em espécie',  Icon: Banknote     },
] as const;

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}
function formatPlate(value: string) { return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7); }
function formatRenavam(value: string) { return value.replace(/\D/g, '').slice(0, 11); }
function formatChassis(value: string) { return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 17); }


// ---------------------------------------------------------------------------
// TrackerAutocomplete — busca em tempo real por IMEI, marca ou modelo
// ---------------------------------------------------------------------------
function TrackerAutocomplete({
  trackers,
  value,
  onChange,
  required,
}: {
  trackers: TrackerOption[];
  value: string;
  onChange: (id: string) => void;
  required?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = trackers.find((t) => String(t.id) === value);

  useEffect(() => { if (!value) setQuery(''); }, [value]);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false); setFocused(false);
      }
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = query.trim()
    ? trackers.filter((t) => {
        const q = query.trim().toLowerCase();
        return (
          t.imei?.toLowerCase().includes(q) ||
          t.brand?.toLowerCase().includes(q) ||
          t.model?.toLowerCase().includes(q)
        );
      })
    : trackers.slice(0, 15);

  function select(t: TrackerOption) {
    onChange(String(t.id));
    setQuery(''); setOpen(false); setFocused(false);
  }

  function clear() {
    onChange(''); setQuery('');
    inputRef.current?.focus();
  }

  const label = (t: TrackerOption) => [t.brand, t.model].filter(Boolean).join(' ');

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Chip do rastreador selecionado */}
      {selected && !focused ? (
        <div className="flex items-center gap-2 rounded-xl border border-brand-300 bg-brand-50 px-3.5 py-2.5 dark:border-brand-700 dark:bg-brand-950/30">
          <div className="min-w-0 flex-1">
            <p className="font-mono text-sm font-semibold text-brand-800 dark:text-brand-200">{selected.imei}</p>
            {label(selected) && <p className="text-xs text-brand-600 dark:text-brand-400">{label(selected)}</p>}
          </div>
          <button
            type="button"
            onClick={clear}
            className="shrink-0 rounded p-0.5 text-brand-500 hover:bg-brand-100 hover:text-brand-700 dark:hover:bg-brand-900/60"
            aria-label="Remover seleção"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            required={required && !value}
            placeholder={selected ? selected.imei : 'Buscar por IMEI, marca ou modelo…'}
            autoComplete="off"
            onFocus={() => { setFocused(true); setOpen(true); }}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onBlur={() => {
              setTimeout(() => {
                if (!containerRef.current?.contains(document.activeElement)) {
                  setOpen(false); setFocused(false);
                }
              }, 150);
            }}
            className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400"
          />
        </div>
      )}

      {/* Dropdown de resultados */}
      {open && focused && (
        <div className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900">
          {filtered.length === 0 ? (
            <div className="px-4 py-3 text-sm text-slate-400">
              {query.trim() ? `Nenhum rastreador encontrado para "${query}"` : 'Nenhum rastreador em estoque'}
            </div>
          ) : (
            filtered.map((t) => (
              <button
                key={t.id}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); select(t); }}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-mono font-semibold text-slate-900 dark:text-white">{t.imei}</p>
                  {label(t) && <p className="text-xs text-slate-400">{label(t)}</p>}
                </div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// "Vincular rastreador ao veículo" — componente isolado para useMemo limpo
// ---------------------------------------------------------------------------
function LinkTrackerForm({
  stockTrackers,
  plans,
  serviceProducts,
  clients,
  vehicleClientId,
  form,
  onChange,
  selectedProductIds,
  onProductToggle,
  onSubmit,
  onCancel,
  linking,
  fieldClass,
}: {
  stockTrackers: TrackerOption[];
  plans: PlanOption[];
  serviceProducts: ServiceProductOption[];
  clients: ClientOption[];
  vehicleClientId?: number;
  form: {
    tracker_id: string; plan_id: string; payment_method: string; billing_day: string; start_date: string;
    use_client_as_interveniente: string; interveniente_client_id: string; bank: string;
  };
  onChange: (f: typeof form) => void;
  selectedProductIds: number[];
  onProductToggle: (id: number) => void;
  onSubmit: () => void;
  onCancel: () => void;
  linking: boolean;
  fieldClass: string;
}) {
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...form, [key]: e.target.value });


  const selectedPlan = plans.find((p) => String(p.id) === form.plan_id);
  const isMonthlyPlan = !selectedPlan?.billing_interval_months || selectedPlan.billing_interval_months === 1;

  // Estimated first billing date (billing_day of next month)
  const firstBillingDate = (() => {
    const day = form.billing_day ? parseInt(form.billing_day) : 1;
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth() + 1, day).toLocaleDateString('pt-BR');
  })();

  // Pré-visualização da 1ª fatura (pró-rata)
  const preview = (() => {
    if (!selectedPlan || !form.start_date) return null;
    const d = new Date(form.start_date + 'T12:00:00');
    const day = d.getDate();
    if (day === 1) return null;
    const daysInMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    const remaining = daysInMonth - day + 1;
    const prorated = (selectedPlan.price / daysInMonth) * remaining;
    const productsFee = selectedProductIds.reduce((sum, pid) => {
      const p = serviceProducts.find((sp) => sp.id === pid);
      return sum + (p?.default_price ?? 0);
    }, 0);
    return { prorated, productsFee, total: prorated + productsFee, remaining, daysInMonth };
  })();

  return (
    <div className="space-y-6">
      {/* ── Equipamento ──────────────────────────────────────────────────── */}
      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Equipamento</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField label="Rastreador disponível" required>
            <TrackerAutocomplete
              trackers={stockTrackers}
              value={form.tracker_id}
              onChange={(id) => onChange({ ...form, tracker_id: id })}
              required
            />
          </FormField>
          <FormField label="Data de instalação" required>
            <input className={fieldClass} type="date" value={form.start_date} onChange={set('start_date')} />
          </FormField>
        </div>
      </div>

      {/* ── Interveniente financeiro ─────────────────────────────────────── */}
      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Interveniente financeiro</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField label="Utilizar cliente como interveniente?">
            <select
              className={fieldClass}
              value={form.use_client_as_interveniente}
              onChange={(e) => onChange({ ...form, use_client_as_interveniente: e.target.value, interveniente_client_id: '' })}
            >
              <option value="sim">Sim — o próprio cliente paga</option>
              <option value="nao">Não — outra pessoa responde pela cobrança</option>
            </select>
          </FormField>
          {form.use_client_as_interveniente === 'nao' && (
            <FormField label="Interveniente" required>
              <ClientAutocomplete
                clients={clients.filter((c) => c.id !== vehicleClientId)}
                value={form.interveniente_client_id}
                onChange={(id) => onChange({ ...form, interveniente_client_id: id })}
                placeholder="Buscar cliente por nome ou CPF/CNPJ…"
                required
              />
            </FormField>
          )}
        </div>
        {form.use_client_as_interveniente === 'nao' && (
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
            O interveniente precisa estar cadastrado como cliente — as cobranças deste contrato ficam vinculadas a ele, sem duplicar cadastro.
          </p>
        )}
      </div>

      {/* ── Plano de monitoramento ───────────────────────────────────────── */}
      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Plano de monitoramento</p>
        <select className={fieldClass} value={form.plan_id} onChange={set('plan_id')}>
          <option value="">Sem plano agora</option>
          {plans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} — R$ {Number(p.price).toFixed(2)}{pricePeriodSuffix(p.billing_interval_months)}
            </option>
          ))}
        </select>
      </div>

      {/* ── Produtos / Serviços adicionais ────────────────────────────────── */}
      {serviceProducts.length > 0 && (
        <div>
          <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Serviços adicionais</p>
          <p className="mb-3 text-xs text-slate-400 dark:text-slate-500">
            Serviços cobrados uma única vez junto à primeira fatura.
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {serviceProducts.map((prod) => {
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
                    onChange={() => onProductToggle(prod.id)}
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

      {/* ── Cobrança: banco emissor + dia do vencimento ─────────────────── */}
      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Cobrança</p>
        <FormField label="Banco (emissão do boleto)">
          <select className={fieldClass} value={form.bank} onChange={set('bank')}>
            <option value="ailos">Ailos (Viacredi) — 085</option>
            <option value="outro">Outro banco (emissão manual)</option>
          </select>
        </FormField>
        <p className="mb-3 mt-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Dia do vencimento</p>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/50">
          <div className="flex items-center justify-between gap-4">
            <div>
              {form.billing_day ? (
                <p className="text-base font-bold text-slate-900 dark:text-white">
                  Todo dia <span className="text-brand-700 dark:text-brand-300">{form.billing_day}</span>
                </p>
              ) : (
                <p className="text-sm text-amber-600 dark:text-amber-400">
                  Não configurado no cadastro do cliente
                </p>
              )}
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                Definido no cadastro do cliente · unifica todos os veículos
              </p>
            </div>
            <div className="shrink-0">
              <BillingDayInput
                value={form.billing_day}
                onChange={(v) => onChange({ ...form, billing_day: v })}
                placeholder="Dia"
                className="w-24 text-center"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ── Forma de pagamento ───────────────────────────────────────────── */}
      {form.plan_id && (
        <div>
          <p className="mb-3 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">Como o cliente vai pagar?</p>
          <div className="space-y-4">
            {/* Opções de pagamento */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
              {PAYMENT_METHODS.map(({ value, label, desc, Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => onChange({ ...form, payment_method: value })}
                  className={[
                    'flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-center text-sm font-medium transition-colors',
                    form.payment_method === value
                      ? 'border-brand-500 bg-brand-50 text-brand-700 ring-1 ring-brand-500/20 dark:border-brand-400 dark:bg-brand-950/30 dark:text-brand-300'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800',
                  ].join(' ')}
                >
                  <Icon className={`h-5 w-5 ${form.payment_method === value ? 'text-brand-600 dark:text-brand-400' : 'text-slate-400 dark:text-slate-500'}`} />
                  <span className="text-xs font-semibold leading-tight">{label}</span>
                  <span className="text-[10px] text-slate-400 dark:text-slate-500 leading-tight">{desc}</span>
                </button>
              ))}
            </div>

            {/* Plano mensal: exibe data estimada da 1ª cobrança */}
            {isMonthlyPlan && (
              <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50/60 px-4 py-3 text-sm dark:border-emerald-800/50 dark:bg-emerald-950/20">
                <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/40">
                  <svg className="h-3 w-3 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                </div>
                <div>
                  <p className="font-semibold text-emerald-700 dark:text-emerald-400">Cobrança mensal recorrente</p>
                  <p className="mt-0.5 text-slate-600 dark:text-slate-400">
                    1ª cobrança estimada: <strong className="text-slate-800 dark:text-slate-200">{firstBillingDate}</strong>
                  </p>
                  <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                    A cobrança se repete automaticamente todo mês nessa data.
                  </p>
                </div>
              </div>
            )}


            {/* Pré-visualização da 1ª fatura (pró-rata) */}
            {preview && (
              <div className="rounded-xl border border-brand-200 bg-brand-50/60 p-4 text-sm dark:border-brand-800/60 dark:bg-brand-950/30">
                <p className="mb-3 text-xs font-bold uppercase tracking-widest text-brand-600 dark:text-brand-400">
                  1ª fatura — pró-rata ({preview.remaining} de {preview.daysInMonth} dias)
                </p>
                <div className="space-y-1.5 text-slate-700 dark:text-slate-300">
                  <div className="flex justify-between">
                    <span>Mensalidade proporcional</span>
                    <span className="font-mono font-medium">R$ {preview.prorated.toFixed(2)}</span>
                  </div>
                  {selectedProductIds.map((pid) => {
                    const prod = serviceProducts.find((sp) => sp.id === pid);
                    if (!prod) return null;
                    return (
                      <div key={pid} className="flex justify-between text-slate-500">
                        <span>{prod.name}</span>
                        <span className="font-mono">R$ {Number(prod.default_price).toFixed(2)}</span>
                      </div>
                    );
                  })}
                  <div className="mt-2 flex justify-between border-t border-brand-200 pt-2.5 font-bold dark:border-brand-800/60">
                    <span>Total 1ª fatura</span>
                    <span className="font-mono text-brand-700 dark:text-brand-300">R$ {preview.total.toFixed(2)}</span>
                  </div>
                </div>
                <p className="mt-2.5 text-xs text-slate-400 dark:text-slate-500">
                  {isMonthlyPlan ? 'A partir do mês seguinte' : 'Nas próximas cobranças'}:{' '}
                  R$ {selectedPlan?.price?.toFixed(2)}{pricePeriodSuffix(selectedPlan?.billing_interval_months)}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
        <Button variant="secondary" onClick={onCancel}>Cancelar</Button>
        <Button
          disabled={!form.tracker_id || linking || (form.use_client_as_interveniente === 'nao' && !form.interveniente_client_id)}
          onClick={onSubmit}
        >
          {linking ? 'Vinculando…' : 'Confirmar vínculo'}
        </Button>
      </div>
    </div>
  );
}

export default function VeiculosPage() {
  const { token, user, loading: guardLoading, error: guardError } = useAuthGuard(ROUTE_ROLES['/veiculos'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [contracts, setContracts] = useState<ContractOption[]>([]);
  const [serviceProducts, setServiceProducts] = useState<ServiceProductOption[]>([]);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [vehicleDocuments, setVehicleDocuments] = useState<VehicleDocument[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [clientFilter, setClientFilter] = useState('');
  const [form, setForm] = useState<VehicleFormState>(initialForm);
  const [modalOpen, setModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lookingUpCep, setLookingUpCep] = useState(false);
  const [docCategory, setDocCategory] = useState('crlv');
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uninstallOpen, setUninstallOpen] = useState(false);
  const [uninstallDate, setUninstallDate] = useState(new Date().toISOString().slice(0, 10));
  const [uninstallServiceProductId, setUninstallServiceProductId] = useState('');
  const [uninstallFee, setUninstallFee] = useState('');
  // Detalhes (abas)
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<'dados' | 'documentos' | 'rastreador'>('dados');
  // Modal "Ver rastreadores do veículo" (botão 🟢)
  const [trackerViewOpen, setTrackerViewOpen] = useState(false);
  // Modal boletos do veículo (botão 🔵)
  const [billingViewOpen, setBillingViewOpen] = useState(false);
  const [vehicleBillings, setVehicleBillings] = useState<{ id: number; title?: string | null; amount: number; due_date: string; status: string }[]>([]);
  // Modal ordens de serviço do veículo (botão 🟣)
  const [ordersViewOpen, setOrdersViewOpen] = useState(false);
  const [vehicleOrders, setVehicleOrders] = useState<{ id: number; number: string; type: string; status: string; scheduled_at?: string | null }[]>([]);
  // Rastreadores e vínculo
  const [linkedTrackers, setLinkedTrackers] = useState<TrackerOption[]>([]);
  const [stockTrackers, setStockTrackers] = useState<TrackerOption[]>([]);
  const [plans, setPlans] = useState<PlanOption[]>([]);
  const [linkTrackerOpen, setLinkTrackerOpen] = useState(false);
  const [selectedProductIds, setSelectedProductIds] = useState<number[]>([]);
  const [linkForm, setLinkForm] = useState({
    tracker_id: '',
    plan_id: '',
    payment_method: 'boleto',
    billing_day: '',
    start_date: new Date().toISOString().slice(0, 10),
    use_client_as_interveniente: 'sim',
    interveniente_client_id: '',
    bank: 'ailos',
  });
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [modalError, setModalError] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);

  async function loadVehicles(currentToken: string) {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set('search', search.trim());
      if (statusFilter) query.set('status', statusFilter);
      if (clientFilter) query.set('client_id', clientFilter);
      query.set('limit', '300');

      const [vehicleResponse, clientResponse, contractResponse, serviceProductResponse] = await Promise.all([
        apiFetch<Vehicle[]>(`/vehicles?${query.toString()}`, {}, currentToken),
        apiFetch<ClientOption[]>('/clients?limit=300', {}, currentToken),
        apiFetch<ContractOption[]>('/contracts', {}, currentToken).catch(() => []),
        apiFetch<ServiceProductOption[]>('/service-products', {}, currentToken).catch(() => []),
      ]);
      setVehicles(vehicleResponse);
      setClients(clientResponse);
      setContracts(contractResponse.filter((item) => item.status === 'ativo'));
      setServiceProducts(serviceProductResponse);
      if (selectedVehicle) setSelectedVehicle(vehicleResponse.find((item) => item.id === selectedVehicle.id) || null);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  async function openDetails(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setDetailsTab('dados');
    setDetailsOpen(true);
    if (token) {
      const [trackers, plansData] = await Promise.all([
        apiFetch<TrackerOption[]>(`/trackers?vehicle_id=${vehicle.id}&limit=20`, {}, token).catch(() => []),
        apiFetch<PlanOption[]>('/plans', {}, token).catch(() => []),
        loadDocuments(token, vehicle.id),
      ]);
      setLinkedTrackers(trackers);
      setPlans(plansData);
    }
  }

  function _billingDayFromClient(vehicleClientId: number): string {
    const c = clients.find((cl) => cl.id === vehicleClientId);
    return c?.billing_day ? String(c.billing_day) : '';
  }

  async function openTrackerView(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setTrackerViewOpen(true);
    // Pré-popula billing_day do cliente assim que abre o modal
    setLinkForm((p) => ({ ...p, billing_day: _billingDayFromClient(vehicle.client_id) }));
    if (token) {
      const [trackers, plansData] = await Promise.all([
        apiFetch<TrackerOption[]>(`/trackers?vehicle_id=${vehicle.id}&limit=20`, {}, token).catch(() => []),
        apiFetch<PlanOption[]>('/plans', {}, token).catch(() => []),
      ]);
      setLinkedTrackers(trackers);
      setPlans(plansData);
    }
  }

  async function openBillingView(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setBillingViewOpen(true);
    if (token) {
      const bills = await apiFetch<typeof vehicleBillings>(
        `/billings?vehicle_id=${vehicle.id}&limit=30`, {}, token
      ).catch(() => []);
      setVehicleBillings(bills);
    }
  }

  async function openOrdersView(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setOrdersViewOpen(true);
    if (token) {
      const orders = await apiFetch<typeof vehicleOrders>(
        `/service-orders?vehicle_id=${vehicle.id}&limit=20`, {}, token
      ).catch(() => []);
      setVehicleOrders(orders);
    }
  }

  function openLinkFromTrackerView() {
    setTrackerViewOpen(false);
    loadStockTrackers();
    // billing_day já foi populado no openTrackerView — mantém o valor
    setLinkTrackerOpen(true);
  }

  async function loadStockTrackers() {
    if (!token) return;
    try {
      const trackers = await apiFetch<TrackerOption[]>('/trackers?status=em_estoque&limit=200', {}, token);
      setStockTrackers(trackers);
    } catch { setStockTrackers([]); }
  }

  async function linkTracker() {
    if (!token || !selectedVehicle || !linkForm.tracker_id) return;
    setLinking(true);
    try {
      const result = await apiFetch<{ tracker: TrackerOption; contract: { id: number } | null; message: string }>(
        `/trackers/${linkForm.tracker_id}/link-vehicle`,
        {
          method: 'POST',
          body: JSON.stringify({
            vehicle_id: selectedVehicle.id,
            plan_id: linkForm.plan_id ? Number(linkForm.plan_id) : null,
            payment_method: linkForm.payment_method,
            billing_day: linkForm.billing_day ? Number(linkForm.billing_day) : undefined,
            billing_modality: 'boleto',
            start_date: linkForm.start_date,
            interveniente_client_id:
              linkForm.use_client_as_interveniente === 'nao' && linkForm.interveniente_client_id
                ? Number(linkForm.interveniente_client_id)
                : null,
            bank: linkForm.bank,
          }),
        },
        token,
      );

      // Cria ClientChargeItem para cada serviço adicional selecionado
      if (selectedProductIds.length > 0 && result.contract) {
        await Promise.all(
          selectedProductIds.map((pid) => {
            const product = serviceProducts.find((sp) => sp.id === pid);
            if (!product) return Promise.resolve();
            return apiFetch(
              '/client-charge-items',
              {
                method: 'POST',
                body: JSON.stringify({
                  client_id: selectedVehicle.client_id,
                  contract_id: result.contract!.id,
                  vehicle_id: selectedVehicle.id,
                  service_product_id: pid,
                  title: product.name,
                  quantity: 1,
                  unit_price: product.default_price,
                  installment_count: 1,
                  start_date: linkForm.start_date,
                  remove_after_payment: true,
                }),
              },
              token,
            );
          }),
        );
      }

      setFeedback('Rastreador vinculado com sucesso.');
      setLinkTrackerOpen(false);
      setSelectedProductIds([]);
      setLinkForm({
        tracker_id: '', plan_id: '', payment_method: 'boleto',
        billing_day: '',
        start_date: new Date().toISOString().slice(0, 10),
        use_client_as_interveniente: 'sim',
        interveniente_client_id: '',
        bank: 'ailos',
      });
      await loadVehicles(token);
      const updated = await apiFetch<TrackerOption[]>(`/trackers?vehicle_id=${selectedVehicle.id}`, {}, token).catch(() => []);
      setLinkedTrackers(updated);
    } catch (err) { setError(parseError(err)); }
    finally { setLinking(false); }
  }

  async function loadDocuments(currentToken: string, vehicleId: number) {
    try {
      const response = await apiFetch<VehicleDocument[]>(`/vehicles/${vehicleId}/documents`, {}, currentToken);
      setVehicleDocuments(response);
    } catch (err) {
      setError(parseError(err));
    }
  }

  useEffect(() => {
    if (!token) return;
    loadVehicles(token);
  }, [token]);

  // Busca/filtros dinâmicos (sem precisar clicar em "Filtrar")
  const searchDebounced = useDebouncedValue(search);
  useEffectSkipFirst(() => {
    if (token) loadVehicles(token);
  }, [searchDebounced, statusFilter, clientFilter]);

  useEffect(() => {
    if (!token || !selectedVehicle) {
      setVehicleDocuments([]);
      return;
    }
    loadDocuments(token, selectedVehicle.id);
  }, [token, selectedVehicle?.id]);

  const stats = useMemo(() => ({
    total: vehicles.length,
    active: vehicles.filter((item) => item.status === 'ativo').length,
    withoutTracker: vehicles.filter((item) => item.status === 'sem_rastreador').length,
    removed: vehicles.filter((item) => item.status === 'retirado').length,
  }), [vehicles]);

  function resetForm() {
    setForm(initialForm);
    setDocFiles([]);
    setDocCategory('crlv');
    setIsEditing(false);
  }

  function populateFromClient(clientId: string, useAddress = false) {
    const selectedClient = clients.find((item) => item.id === Number(clientId));
    if (!selectedClient || !useAddress) return;
    setForm((prev) => ({
      ...prev,
      // Client.zip_code (não "address_zip_code" — só o formulário do veículo
      // usa esse nome; o cliente vem de ClientOut, que chama o campo de
      // zip_code). Antes disso o CEP nunca era pré-preenchido ao marcar
      // "usar endereço do cliente" — os demais campos de endereço funcionavam
      // normalmente porque os nomes já coincidiam.
      address_zip_code: selectedClient.zip_code ? formatZipCode(selectedClient.zip_code) : prev.address_zip_code,
      address_line: selectedClient.address_line || prev.address_line,
      address_number: selectedClient.address_number || prev.address_number,
      address_complement: selectedClient.address_complement || prev.address_complement,
      neighborhood: selectedClient.neighborhood || prev.neighborhood,
      city: selectedClient.city || prev.city,
      state: selectedClient.state || prev.state,
    }));
  }

  function openCreateModal() {
    resetForm();
    setModalError('');
    setModalOpen(true);
  }

  function openEditModal(vehicle: Vehicle) {
    setSelectedVehicle(vehicle);
    setModalError('');
    setIsEditing(true);
    setForm({
      client_id: String(vehicle.client_id || ''),
      sales_point: vehicle.sales_point || 'MASTERSAT RASTREAMENTO',
      seller_consultant: vehicle.seller_consultant || '',
      vehicle_classification: vehicle.vehicle_classification || 'NAO INFORMADO',
      user_alert: vehicle.user_alert || 'Nenhum',
      contract_number: vehicle.contract_number || '',
      contract_date: vehicle.contract_date || '',
      contract_end_date: vehicle.contract_end_date || '',
      address_zip_code: vehicle.address_zip_code ? formatZipCode(vehicle.address_zip_code) : '',
      address_line: vehicle.address_line || '',
      address_number: vehicle.address_number || '',
      address_complement: vehicle.address_complement || '',
      neighborhood: vehicle.neighborhood || '',
      city: vehicle.city || '',
      state: vehicle.state || '',
      plate: vehicle.plate || '',
      chassis: vehicle.chassis || '',
      renavam: vehicle.renavam || '',
      brand: vehicle.brand || '',
      model: vehicle.model || '',
      manufacture_year: vehicle.manufacture_year ? String(vehicle.manufacture_year) : '',
      model_year: vehicle.model_year ? String(vehicle.model_year) : '',
      color: vehicle.color || '',
      fuel_type: vehicle.fuel_type || '',
      type: vehicle.type || '',
      status: vehicle.status,
    });
    setDocFiles([]);
    setModalOpen(true);
  }

  async function fillAddressByCep(value: string) {
    const cep = onlyDigits(value);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    try {
      const address = await fetchAddressByCep(cep);
      if (address) {
        setForm((prev) => ({
          ...prev,
          address_zip_code: formatZipCode(cep),
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

  async function submitVehicle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !canEdit) return;
    setSaving(true);
    setModalError('');
    setFeedback('');
    try {
      const payload = {
        client_id: Number(form.client_id),
        sales_point: form.sales_point || null,
        seller_consultant: form.seller_consultant || null,
        vehicle_classification: form.vehicle_classification || null,
        user_alert: form.user_alert || null,
        contract_number: form.contract_number || null,
        contract_date: form.contract_date || null,
        contract_end_date: form.contract_end_date || null,
        address_zip_code: onlyDigits(form.address_zip_code) || null,
        address_line: form.address_line || null,
        address_number: form.address_number || null,
        address_complement: form.address_complement || null,
        neighborhood: form.neighborhood || null,
        city: form.city || null,
        state: form.state || null,
        plate: formatPlate(form.plate),
        chassis: formatChassis(form.chassis) || null,
        renavam: formatRenavam(form.renavam) || null,
        brand: form.brand || null,
        model: form.model || null,
        manufacture_year: form.manufacture_year ? Number(form.manufacture_year) : null,
        model_year: form.model_year ? Number(form.model_year) : null,
        color: form.color || null,
        fuel_type: form.fuel_type || null,
        type: form.type || null,
        status: form.status,
      };

      const saved = isEditing && selectedVehicle
        ? await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, { method: 'PUT', body: JSON.stringify(payload) }, token)
        : await apiFetch<Vehicle>('/vehicles', { method: 'POST', body: JSON.stringify(payload) }, token);

      if (docFiles.length) {
        const body = new FormData();
        body.append('category', docCategory);
        docFiles.forEach((file) => body.append('files', file));
        await apiFetch(`/vehicles/${saved.id}/documents`, { method: 'POST', body }, token);
      }

      setFeedback(isEditing ? 'Veículo atualizado com sucesso.' : 'Veículo cadastrado com sucesso.');
      setModalOpen(false);
      resetForm();
      await loadVehicles(token);
      setSelectedVehicle(saved);
      await loadDocuments(token, saved.id);
    } catch (err) {
      setModalError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function uploadDocuments() {
    if (!token || !selectedVehicle || !canEdit || !docFiles.length) return;
    setUploading(true);
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents`, { method: 'POST', body }, token);
      await loadDocuments(token, selectedVehicle.id);
      setDocFiles([]);
      setFeedback('Documentos enviados com sucesso.');
    } catch (err) {
      setError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function removeDocument(documentId: number) {
    if (!token || !selectedVehicle || !canEdit) return;
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents/${documentId}`, { method: 'DELETE' }, token);
      await loadDocuments(token, selectedVehicle.id);
      setFeedback('Documento removido com sucesso.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function reviewDocument(documentId: number, reviewStatus: string) {
    if (!token || !selectedVehicle || !canEdit) return;
    const reviewNotes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/vehicles/${selectedVehicle.id}/documents/${documentId}/review`, {
        method: 'POST',
        body: JSON.stringify({ review_status: reviewStatus, review_notes: reviewNotes || null }),
      }, token);
      await loadDocuments(token, selectedVehicle.id);
      setFeedback('Status do documento atualizado.');
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function deleteVehicle(vehicleId: number) {
    if (!token || !canEdit) return;
    if (!window.confirm('Deseja remover este veículo?')) return;
    try {
      await apiFetch(`/vehicles/${vehicleId}`, { method: 'DELETE' }, token);
      setFeedback('Veículo removido com sucesso.');
      if (selectedVehicle?.id === vehicleId) setSelectedVehicle(null);
      await loadVehicles(token);
    } catch (err) {
      setError(parseError(err));
    }
  }

  async function confirmUninstall() {
    if (!token || !selectedVehicle || !canEdit) return;
    setModalError('');
    try {
      const params = new URLSearchParams();
      if (uninstallDate) params.set('uninstall_date', uninstallDate);
      if (uninstallServiceProductId) params.set('uninstall_service_product_id', uninstallServiceProductId);
      if (uninstallFee && parseFloat(uninstallFee) > 0) params.set('uninstall_fee', uninstallFee);
      await apiFetch(`/vehicles/${selectedVehicle.id}/uninstall?${params.toString()}`, { method: 'POST' }, token);
      setFeedback('Desinstalação registrada com sucesso.');
      setUninstallFee('');
      setUninstallOpen(false);
      await loadVehicles(token);
      const updated = await apiFetch<Vehicle>(`/vehicles/${selectedVehicle.id}`, {}, token);
      setSelectedVehicle(updated);
    } catch (err) {
      setModalError(parseError(err));
    }
  }

  return (
    <PageShell title="Veículos" description="Gestão dos ativos da frota com cadastro em modal, documentação, desinstalação e visão consolidada por cliente.">
      {(guardError || error || feedback) && (
        <div className="mb-4 space-y-3">
          {(guardError || error) ? <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{guardError || error}</p> : null}
          {feedback ? <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p> : null}
        </div>
      )}
      {guardLoading ? <p className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">Validando sessão...</p> : null}

      <section>
        <Card>
          <SectionHeader
            eyebrow="Cadastro"
            title="Frota cadastrada"
            description="Gestão completa de veículos, rastreadores vinculados e documentação."
            actions={
              <div className="flex items-center gap-2">
                {token && <ExportButton path="exports/vehicles" basename="veiculos" token={token} params={{ status: statusFilter, client_id: clientFilter }} />}
                {canEdit && <Button type="button" onClick={() => setWizardOpen(true)}>Adicionar veículo</Button>}
              </div>
            }
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <input className={fieldClass} style={{ maxWidth: 280 }} placeholder="Buscar por placa, chassi ou modelo" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className={fieldClass} style={{ width: 180 }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Todos os status</option>
              <option value="ativo">Ativo</option>
              <option value="sem_rastreador">Sem rastreador</option>
              <option value="retirado">Retirado</option>
              <option value="bloqueado">Bloqueado</option>
            </select>
            <div style={{ width: 260 }}>
              <ClientAutocomplete
                clients={clients}
                value={clientFilter}
                onChange={setClientFilter}
                placeholder="Todos os clientes"
              />
            </div>
            <Button type="button" variant="secondary" onClick={() => token && loadVehicles(token)} disabled={loading}>
              {loading ? 'Atualizando…' : 'Atualizar'}
            </Button>
          </div>

          <div className="mt-4">
            {loading ? (
              <TableSkeleton rows={7} cols={5} />
            ) : error ? (
              <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os veículos" description="Veja o erro acima e tente novamente." />
            ) : vehicles.length === 0 ? (
              <EmptyState title="Nenhum veículo encontrado" description="Ajuste os filtros ou cadastre o primeiro veículo." action={canEdit ? <Button onClick={() => setWizardOpen(true)}>Adicionar veículo</Button> : undefined} />
            ) : (
              <Table>
                <TableHead>
                  <Th>Placa</Th>
                  <Th>Veículo</Th>
                  <Th>Cliente</Th>
                  <Th>Status</Th>
                  <Th className="w-44" />
                </TableHead>
                <TableBody>
                  {vehicles.map((vehicle) => {
                    const client = clients.find((c) => c.id === vehicle.client_id);
                    return (
                      <Tr key={vehicle.id}>
                        <Td className="font-mono font-semibold">{vehicle.plate}</Td>
                        <Td>
                          <p>{[vehicle.brand, vehicle.model].filter(Boolean).join(' ')}</p>
                          <p className="text-xs text-slate-400">{vehicle.model_year ?? vehicle.manufacture_year ?? '—'} · {vehicle.type ?? '—'}</p>
                        </Td>
                        <Td>{client?.name ?? '—'}</Td>
                        <Td><Badge variant={statusVariant(vehicle.status)}>{statusLabel(vehicle.status)}</Badge></Td>
                        <Td>
                          <div className="flex justify-end gap-1">
                            {/* 🟢 Ver rastreadores */}
                            <button
                              title="Ver rastreadores vinculados"
                              onClick={() => openTrackerView(vehicle)}
                              className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-400 dark:hover:bg-emerald-950/60"
                            >
                              <Eye className="h-4 w-4" />
                            </button>
                            {/* 🔵 Boletos */}
                            <button
                              title="Boletos do veículo"
                              onClick={() => openBillingView(vehicle)}
                              className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-400 dark:hover:bg-blue-950/60"
                            >
                              <DollarSign className="h-4 w-4" />
                            </button>
                            {/* 🟣 Ordens de serviço */}
                            <button
                              title="Ordens de serviço do veículo"
                              onClick={() => openOrdersView(vehicle)}
                              className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-50 text-purple-600 hover:bg-purple-100 dark:bg-purple-950/40 dark:text-purple-400 dark:hover:bg-purple-950/60"
                            >
                              <ClipboardList className="h-4 w-4" />
                            </button>
                            {/* Editar cadastro */}
                            {canEdit && (
                              <button
                                title="Editar veículo"
                                onClick={() => openEditModal(vehicle)}
                                className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:bg-slate-800"
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
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

      {/* Indicadores abaixo do cadastro (padrão de todas as telas) */}
      <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Veículos cadastrados" value={stats.total} hint="Base total de ativos" icon="🚗" />
        <StatCard label="Em operação" value={stats.active} hint="Status ativo/aprovado" tone="success" icon="✅" />
        <StatCard label="Sem rastreador" value={stats.withoutTracker} hint="Exigem ação técnica" tone="warning" icon="📍" />
        <StatCard label="Retirados" value={stats.removed} hint="Veículos desinstalados" tone="danger" icon="↩️" />
      </section>

      {/* Modal de detalhes */}
      <Modal
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedVehicle(null); setLinkedTrackers([]); }}
        title={selectedVehicle?.plate ?? ''}
        subtitle="Detalhes do veículo"
        size="xl"
      >
        {selectedVehicle && (
          <div className="space-y-4">
            {/* Abas */}
            <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
              {(['dados', 'rastreador', 'documentos'] as const).map((tab) => (
                <button key={tab} type="button" onClick={() => setDetailsTab(tab)}
                  className={`px-4 py-2 text-sm font-medium transition-colors ${detailsTab === tab ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                  {tab === 'dados' ? 'Dados' : tab === 'rastreador' ? 'Rastreador' : 'Documentos'}
                </button>
              ))}
            </div>

            {/* Aba Dados */}
            {detailsTab === 'dados' && (
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  ['Cliente', clients.find((c) => c.id === selectedVehicle.client_id)?.name ?? '—'],
                  ['Contrato', selectedVehicle.contract_number ?? '—'],
                  ['Chassi', selectedVehicle.chassis ?? '—'],
                  ['Renavam', selectedVehicle.renavam ?? '—'],
                  ['Marca / Modelo', [selectedVehicle.brand, selectedVehicle.model].filter(Boolean).join(' ') || '—'],
                  ['Ano', `${selectedVehicle.manufacture_year ?? '—'} / ${selectedVehicle.model_year ?? '—'}`],
                  ['Cor', selectedVehicle.color ?? '—'],
                  ['Combustível', selectedVehicle.fuel_type ?? '—'],
                  ['Endereço', [selectedVehicle.address_line, selectedVehicle.address_number, selectedVehicle.city, selectedVehicle.state].filter(Boolean).join(', ') || '—'],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">{label}</p>
                    <p className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</p>
                  </div>
                ))}
              </div>
            )}

            {/* Aba Rastreador */}
            {detailsTab === 'rastreador' && (
              <div className="space-y-4">
                {linkedTrackers.length === 0 ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/40 dark:bg-amber-950/30">
                    <p className="text-sm font-medium text-amber-700 dark:text-amber-400">Nenhum rastreador vinculado a este veículo.</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {linkedTrackers.map((t) => (
                      <div key={t.id} className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/30">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="font-mono font-semibold text-slate-900 dark:text-white">{t.imei}</p>
                            <p className="mt-0.5 text-xs text-slate-500">{[t.brand, t.model].filter(Boolean).join(' ')}</p>
                            <Badge variant={statusVariant(t.status)} className="mt-1">{statusLabel(t.status)}</Badge>
                          </div>
                          {canEdit && (
                            <Button variant="danger" onClick={() => { setSelectedVehicle(selectedVehicle); setUninstallOpen(true); }} className="text-xs px-3 py-1.5 shrink-0">
                              Desinstalar
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {canEdit && (
                  <Button onClick={() => { loadStockTrackers(); setLinkTrackerOpen(true); }} className="gap-2 w-full justify-center">
                    + Vincular rastreador
                  </Button>
                )}
              </div>
            )}

            {/* Aba Documentos */}
            {detailsTab === 'documentos' && (
              <div className="space-y-4">
                {canEdit && (
                  <div className="flex flex-wrap gap-2">
                    <select className={fieldClass} style={{ width: 180 }} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
                      {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input type="file" multiple className={`${fieldClass} file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:text-white`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
                    <Button disabled={!docFiles.length || uploading} onClick={uploadDocuments}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
                  </div>
                )}
                {vehicleDocuments.length === 0 ? (
                  <EmptyState title="Nenhum documento" description="Nenhum documento foi anexado a este veículo." />
                ) : (
                  <div className="space-y-2">
                    {vehicleDocuments.map((doc) => (
                      <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p className="text-sm font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                            <p className="text-xs text-slate-400">{doc.category}{doc.review_notes ? ` · ${doc.review_notes}` : ''}</p>
                          </div>
                          <Badge variant={statusVariant(doc.review_status ?? 'enviado')}>{statusLabel(doc.review_status ?? 'enviado')}</Badge>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Visualizar</a>
                          <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Baixar</a>
                          {canEdit && (
                            <>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                              <button type="button" onClick={() => reviewDocument(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                              <button type="button" onClick={() => removeDocument(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
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

      {/* Modal: Vincular rastreador */}
      <Modal
        open={linkTrackerOpen}
        onClose={() => { setLinkTrackerOpen(false); setSelectedProductIds([]); }}
        title="Vincular rastreador ao veículo"
        subtitle={selectedVehicle ? `${selectedVehicle.plate} · ${[selectedVehicle.brand, selectedVehicle.model].filter(Boolean).join(' ') || 'Veículo'}` : 'Vínculo'}
        size="xl"
      >
        <LinkTrackerForm
          stockTrackers={stockTrackers}
          plans={plans}
          serviceProducts={serviceProducts}
          clients={clients}
          vehicleClientId={selectedVehicle?.client_id}
          form={linkForm}
          onChange={setLinkForm}
          selectedProductIds={selectedProductIds}
          onProductToggle={(id) => setSelectedProductIds((prev) =>
            prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
          )}
          onSubmit={linkTracker}
          onCancel={() => { setLinkTrackerOpen(false); setSelectedProductIds([]); }}
          linking={linking}
          fieldClass={fieldClass}
        />
      </Modal>

      {/* ── Modal: Ver rastreadores do veículo (🟢) ─────────────────────── */}
      <Modal
        open={trackerViewOpen}
        onClose={() => { setTrackerViewOpen(false); setSelectedVehicle(null); setLinkedTrackers([]); }}
        title={`Rastreadores — ${selectedVehicle?.plate ?? ''}`}
        subtitle="Visualizar rastreadores do veículo"
        size="xl"
      >
        {selectedVehicle && (
          <div className="space-y-4">
            {linkedTrackers.length === 0 ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">
                Nenhum rastreador vinculado a este veículo.
              </div>
            ) : (
              <Table>
                <TableHead>
                  <Th>Equipamento / IMEI</Th>
                  <Th>Grupo de faturamento</Th>
                  <Th>Tipo</Th>
                  <Th>Data de instalação</Th>
                  <Th className="w-24" />
                </TableHead>
                <TableBody>
                  {linkedTrackers.map((t) => (
                    <Tr key={t.id}>
                      <Td>
                        <p className="font-mono font-semibold text-slate-900 dark:text-white">{t.imei}</p>
                      </Td>
                      <Td>
                        {t.active_plan_name ? (
                          <span className="font-medium text-slate-700 dark:text-slate-300">{t.active_plan_name}</span>
                        ) : (
                          <span className="text-slate-400">Sem plano</span>
                        )}
                      </Td>
                      <Td className="text-sm">{[t.brand, t.model].filter(Boolean).join(' ') || '—'}</Td>
                      <Td>
                        {t.install_date ? (
                          <span className="font-medium text-slate-700 dark:text-slate-300">
                            {new Date(t.install_date + 'T12:00:00').toLocaleDateString('pt-BR')}
                          </span>
                        ) : '—'}
                      </Td>
                      <Td>
                        {canEdit && (
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => {
                                setLinkForm((p) => ({
                                  ...p,
                                  tracker_id: String(t.id),
                                  plan_id: t.active_plan_id ? String(t.active_plan_id) : '',
                                }));
                                setTrackerViewOpen(false);
                                loadStockTrackers();
                                setLinkTrackerOpen(true);
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                              title="Editar vínculo"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => {
                                // Auto-fill uninstall modal with recommended product
                                const recommended = serviceProducts.find(sp => sp.auto_add_on_uninstall);
                                setUninstallServiceProductId(recommended ? String(recommended.id) : '');
                                setUninstallFee(recommended ? '' : '');
                                setUninstallDate(new Date().toISOString().slice(0, 10));
                                setTrackerViewOpen(false);
                                setUninstallOpen(true);
                              }}
                              className="flex h-7 items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2 text-xs font-medium text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400 dark:hover:bg-rose-950/50"
                              title="Desvincular rastreador"
                            >
                              Desvincular
                            </button>
                          </div>
                        )}
                      </Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
            {canEdit && (
              <div className="flex justify-end border-t border-slate-100 pt-4 dark:border-slate-800">
                <Button onClick={openLinkFromTrackerView} className="gap-2">
                  + Vincular rastreador
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* ── Modal: Boletos do veículo (🔵) ──────────────────────────────── */}
      <Modal
        open={billingViewOpen}
        onClose={() => { setBillingViewOpen(false); setSelectedVehicle(null); setVehicleBillings([]); }}
        title={`Boletos — ${selectedVehicle?.plate ?? ''}`}
        subtitle="Cobranças do veículo"
        size="lg"
      >
        {vehicleBillings.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">Nenhuma cobrança encontrada para este veículo.</p>
        ) : (
          <Table>
            <TableHead>
              <Th>Descrição</Th>
              <Th>Vencimento</Th>
              <Th>Valor</Th>
              <Th>Status</Th>
            </TableHead>
            <TableBody>
              {vehicleBillings.map((b) => (
                <Tr key={b.id}>
                  <Td className="text-sm">{b.title ?? '—'}</Td>
                  <Td className="text-sm">{b.due_date}</Td>
                  <Td className="font-mono font-semibold">
                    {new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(b.amount)}
                  </Td>
                  <Td><Badge variant={statusVariant(b.status)}>{statusLabel(b.status)}</Badge></Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
      </Modal>

      {/* ── Modal: Ordens de serviço do veículo (🟣) ────────────────────── */}
      <Modal
        open={ordersViewOpen}
        onClose={() => { setOrdersViewOpen(false); setSelectedVehicle(null); setVehicleOrders([]); }}
        title={`Agendas — ${selectedVehicle?.plate ?? ''}`}
        subtitle="Ordens de serviço do veículo"
        size="lg"
      >
        {vehicleOrders.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">Nenhuma ordem de serviço encontrada para este veículo.</p>
        ) : (
          <Table>
            <TableHead>
              <Th>Número</Th>
              <Th>Tipo</Th>
              <Th>Agendado</Th>
              <Th>Status</Th>
            </TableHead>
            <TableBody>
              {vehicleOrders.map((o) => (
                <Tr key={o.id}>
                  <Td className="font-mono font-semibold">{o.number}</Td>
                  <Td className="text-sm capitalize">{o.type.replace(/_/g, ' ')}</Td>
                  <Td className="text-sm">{o.scheduled_at ? new Date(o.scheduled_at).toLocaleDateString('pt-BR') : '—'}</Td>
                  <Td><Badge variant={statusVariant(o.status)}>{statusLabel(o.status)}</Badge></Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
      </Modal>

      <Modal open={modalOpen} onClose={() => { setModalOpen(false); resetForm(); setModalError(''); }} title={isEditing ? 'Editar veículo' : 'Novo veículo'} description="Mantenha o cadastro técnico e documental do veículo em um fluxo de preenchimento mais limpo." size="2xl">
        <form className="space-y-6" onSubmit={submitVehicle}>
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-3">
            <ClientAutocomplete
              clients={clients}
              value={form.client_id}
              onChange={(id) => setForm((prev) => ({ ...prev, client_id: id }))}
              placeholder="Digite nome ou CPF/CNPJ do cliente…"
              required
            />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-200"><input type="checkbox" onChange={(e) => populateFromClient(form.client_id, e.target.checked)} /> Usar endereço do cliente</label>
            <select className={fieldClass} value={form.status} onChange={(e) => setForm((prev) => ({ ...prev, status: e.target.value as VehicleStatus }))}>{['ativo','sem_rastreador','retirado','bloqueado','pendente_validacao','em_analise','aprovado','reprovado','correcao_solicitada'].map((option) => <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>)}</select>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <select className={fieldClass} value={form.sales_point} onChange={(e) => setForm((prev) => ({ ...prev, sales_point: e.target.value }))}>{salesPointOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.vehicle_classification} onChange={(e) => setForm((prev) => ({ ...prev, vehicle_classification: e.target.value }))}>{classificationOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.user_alert} onChange={(e) => setForm((prev) => ({ ...prev, user_alert: e.target.value }))}>{alertOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <input className={fieldClass} placeholder="Consultor / responsável" value={form.seller_consultant} onChange={(e) => setForm((prev) => ({ ...prev, seller_consultant: e.target.value }))} />
            <input className={fieldClass} placeholder="Número do contrato" value={form.contract_number} onChange={(e) => setForm((prev) => ({ ...prev, contract_number: e.target.value }))} />
            <div className="grid grid-cols-2 gap-4"><input type="date" className={fieldClass} value={form.contract_date} onChange={(e) => setForm((prev) => ({ ...prev, contract_date: e.target.value }))} /><input type="date" className={fieldClass} value={form.contract_end_date} onChange={(e) => setForm((prev) => ({ ...prev, contract_end_date: e.target.value }))} /></div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <input className={fieldClass} placeholder="Placa" value={form.plate} onChange={(e) => setForm((prev) => ({ ...prev, plate: formatPlate(e.target.value) }))} required />
            <input className={fieldClass} placeholder="Chassi" value={form.chassis} onChange={(e) => setForm((prev) => ({ ...prev, chassis: formatChassis(e.target.value) }))} />
            <input className={fieldClass} placeholder="Renavam" value={form.renavam} onChange={(e) => setForm((prev) => ({ ...prev, renavam: formatRenavam(e.target.value) }))} />
            <input className={fieldClass} placeholder="Marca" value={form.brand} onChange={(e) => setForm((prev) => ({ ...prev, brand: e.target.value }))} />
            <input className={fieldClass} placeholder="Modelo" value={form.model} onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))} />
            <div className="grid grid-cols-2 gap-4"><input className={fieldClass} placeholder="Ano fabricação" value={form.manufacture_year} onChange={(e) => setForm((prev) => ({ ...prev, manufacture_year: onlyDigits(e.target.value).slice(0, 4) }))} /><input className={fieldClass} placeholder="Ano modelo" value={form.model_year} onChange={(e) => setForm((prev) => ({ ...prev, model_year: onlyDigits(e.target.value).slice(0, 4) }))} /></div>
            <select className={fieldClass} value={form.type} onChange={(e) => setForm((prev) => ({ ...prev, type: e.target.value }))}><option value="">Tipo de veículo</option>{typeOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.fuel_type} onChange={(e) => setForm((prev) => ({ ...prev, fuel_type: e.target.value }))}><option value="">Combustível</option>{fuelOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
            <select className={fieldClass} value={form.color} onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}><option value="">Cor</option>{colorOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
          </div>
          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <div className="flex gap-2">
                <input className={fieldClass} placeholder="CEP" value={form.address_zip_code} onChange={(e) => setForm((prev) => ({ ...prev, address_zip_code: formatZipCode(e.target.value) }))} onBlur={(e) => fillAddressByCep(e.target.value)} />
                <button type="button" className="rounded-2xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-700 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-200" onClick={() => fillAddressByCep(form.address_zip_code)} disabled={lookingUpCep}>Buscar</button>
              </div>
              {lookingUpCep ? <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Buscando endereço...</p> : null}
            </div>
            <input className="md:col-span-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-950/70 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-cyan-400" placeholder="Logradouro" value={form.address_line} onChange={(e) => setForm((prev) => ({ ...prev, address_line: e.target.value }))} />
            <input className={fieldClass} placeholder="Número" value={form.address_number} onChange={(e) => setForm((prev) => ({ ...prev, address_number: e.target.value }))} />
            <input className={fieldClass} placeholder="Complemento" value={form.address_complement} onChange={(e) => setForm((prev) => ({ ...prev, address_complement: e.target.value }))} />
            <input className={fieldClass} placeholder="Bairro" value={form.neighborhood} onChange={(e) => setForm((prev) => ({ ...prev, neighborhood: e.target.value }))} />
            <input className={fieldClass} placeholder="Cidade" value={form.city} onChange={(e) => setForm((prev) => ({ ...prev, city: e.target.value }))} />
            <input className={fieldClass} placeholder="UF" value={form.state} onChange={(e) => setForm((prev) => ({ ...prev, state: e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))} maxLength={2} />
          </div>
          <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5 dark:border-slate-800 dark:bg-slate-950/60">
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Documentação inicial</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Opcional. Se houver arquivos selecionados, eles serão enviados após salvar o veículo.</p>
            <div className="mt-4 grid gap-4 md:grid-cols-[220px_1fr]">
              <select className={fieldClass} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>{documentCategoryOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select>
              <input type="file" multiple className={`${fieldClass} file:mr-4 file:rounded-xl file:border-0 file:bg-slate-950 file:px-3 file:py-2 file:text-white dark:file:bg-cyan-400 dark:file:text-slate-950`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setModalOpen(false); resetForm(); }}>Cancelar</button>
            <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando...' : isEditing ? 'Atualizar veículo' : 'Cadastrar veículo'}</Button>
          </div>
        </form>
      </Modal>

      {/* ── Wizard: fluxo contínuo de cadastro novo ── */}
      {token && (
        <VehicleOnboardingWizard
          open={wizardOpen}
          token={token}
          clients={clients}
          onComplete={async () => {
            setWizardOpen(false);
            setFeedback('Veículo cadastrado com sucesso.');
            if (token) await loadVehicles(token);
          }}
          onClose={() => setWizardOpen(false)}
        />
      )}

      <Modal open={uninstallOpen} onClose={() => { setUninstallOpen(false); setModalError(''); setUninstallFee(''); setUninstallServiceProductId(''); }} title="Registrar desinstalação" description="Registre a retirada do rastreador. A taxa de desinstalação (se houver) será incluída no próximo fechamento." size="lg">
        <div className="space-y-5">
          {modalError && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{modalError}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Data da desinstalação</span>
              <input type="date" className={`${fieldClass} w-full`} value={uninstallDate} onChange={(e) => setUninstallDate(e.target.value)} />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Valor a cobrar (R$) <span className="font-normal text-slate-400">opcional</span></span>
              <input
                type="number"
                min="0"
                step="0.01"
                placeholder="0,00"
                className={`${fieldClass} w-full`}
                value={uninstallFee}
                onChange={(e) => setUninstallFee(e.target.value)}
              />
            </label>
            <label className="block md:col-span-2">
              <span className="mb-2 block text-sm font-medium text-slate-700 dark:text-slate-200">Serviço de desinstalação <span className="font-normal text-slate-400">opcional</span></span>
              <select
                className={`${fieldClass} w-full`}
                value={uninstallServiceProductId}
                onChange={(e) => {
                  setUninstallServiceProductId(e.target.value);
                  const prod = serviceProducts.find(sp => String(sp.id) === e.target.value);
                  if (prod) setUninstallFee(String(prod.default_price));
                  else setUninstallFee('');
                }}
              >
                <option value="">Sem taxa de desinstalação</option>
                {serviceProducts.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} — R$ {item.default_price.toFixed(2)}{item.auto_add_on_uninstall ? ' ✓ recomendado' : ''}</option>
                ))}
              </select>
            </label>
            {(uninstallServiceProductId || Number(uninstallFee) > 0) && (
              <div className="md:col-span-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">
                Será cobrado {Number(uninstallFee || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })} no fechamento do mês da desinstalação.
                {uninstallServiceProductId && ' O serviço define o que é cobrado; o valor acima é o que vale — ajuste-o para registrar um desconto.'}
              </div>
            )}
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200 dark:hover:border-cyan-400 dark:hover:text-cyan-300" onClick={() => { setUninstallOpen(false); setUninstallFee(''); }}>Cancelar</button>
            <Button type="button" onClick={confirmUninstall}>Confirmar desinstalação</Button>
          </div>
        </div>
      </Modal>
    </PageShell>
  );
}
