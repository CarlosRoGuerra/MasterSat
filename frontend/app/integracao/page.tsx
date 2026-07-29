'use client';

import { useEffect, useState } from 'react';
import { RefreshCw, CheckCircle2, XCircle, AlertTriangle, PlugZap, ChevronDown, ChevronUp } from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StatCard } from '@/components/ui/stat-card';
import { SectionHeader } from '@/components/ui/section-header';
import { TrackerAutocomplete } from '@/components/ui/tracker-autocomplete';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type IntegrationStatus = {
  enabled: boolean;
  wsdl_url?: string | null;
  credentials_configured: boolean;
  group_codes_configured: boolean;
};

type Manufacturer = { code: string; description: string };

type StepResult = {
  operation: string;
  status_code?: string | null;
  status_description?: string | null;
  success: boolean;
  friendly_title?: string | null;
  friendly_message?: string | null;
  severity?: string | null;
};

type FlowOut = {
  provider: string;
  entity_type: string;
  entity_id: number;
  overall_success: boolean;
  steps: StepResult[];
};

type IntegrationLog = {
  id: number;
  entity_type: string;
  entity_id?: number | null;
  operation: string;
  success: boolean;
  response_code?: string | null;
  response_description?: string | null;
  friendly_title?: string | null;
  friendly_message?: string | null;
  severity?: string | null;
  created_at?: string | null;
  batch_id?: string | null;
};

type Tracker = { id: number; imei?: string | null; brand?: string | null; model?: string | null; client_name?: string | null; vehicle_plate?: string | null; integration_status?: string | null };

const fieldClass = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-500 dark:focus:border-brand-400';

function parseError(err: unknown) {
  return err instanceof Error ? err.message : 'Ocorreu um erro inesperado.';
}

function severityClass(severity?: string | null, success?: boolean) {
  if (success || severity === 'success') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (severity === 'warning') return 'border-amber-200 bg-amber-50 text-amber-700';
  return 'border-rose-200 bg-rose-50 text-rose-700';
}

function OperationLabel({ op }: { op: string }) {
  const labels: Record<string, string> = {
    sincronizaCliente: 'Sync Cliente',
    sincronizaUsuario: 'Sync Usuário',
    sincronizaVeiculo: 'Sync Veículo',
    sincronizaEquipamento: 'Sync Equipamento',
    vinculoVeiculoCliente: 'Vínculo Veículo↔Cliente',
    vinculoEquipamentoVeiculo: 'Vínculo Equip↔Veículo',
    trocaEquipamentoVeiculo: 'Troca Equipamento',
    sincronizaChip: 'Sync Chip',
    consultaVinculoEquipamento: 'Consulta Vínculo',
  };
  return <span>{labels[op] || op}</span>;
}

function StepCard({ step }: { step: StepResult }) {
  return (
    <div className={`rounded-2xl border px-4 py-3 ${severityClass(step.severity, step.success)}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold text-sm"><OperationLabel op={step.operation} /></p>
        <span className="rounded-full border px-2 py-0.5 text-xs font-bold uppercase">{step.status_code || '-'}</span>
      </div>
      <p className="mt-1 text-xs">{step.friendly_title || step.status_description || '-'}</p>
      {step.friendly_message && <p className="mt-0.5 text-xs opacity-80">{step.friendly_message}</p>}
    </div>
  );
}

export default function IntegracaoPage() {
  const { token, user } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const canEdit = !!user && user.role !== 'financeiro';

  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([]);
  const [logs, setLogs] = useState<IntegrationLog[]>([]);
  const [trackers, setTrackers] = useState<Tracker[]>([]);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [syncing, setSyncing] = useState<string | null>(null);
  const [lastFlow, setLastFlow] = useState<FlowOut | null>(null);
  const [logFilter, setLogFilter] = useState<'all' | 'success' | 'error'>('all');
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set());
  const [chipTrackerId, setChipTrackerId] = useState('');
  const [chipStatus, setChipStatus] = useState('1');

  async function loadAll(tok: string) {
    try {
      const [st, lg, tr] = await Promise.all([
        apiFetch<IntegrationStatus>('/integrations/multiportal/status', {}, tok),
        apiFetch<IntegrationLog[]>('/integrations/multiportal/logs?limit=50', {}, tok),
        apiFetch<Tracker[]>('/trackers?limit=200', {}, tok),
      ]);
      setStatus(st);
      setLogs(lg);
      setTrackers(tr);
      if (st.enabled) {
        apiFetch<Manufacturer[]>('/integrations/multiportal/manufacturers', {}, tok)
          .then(setManufacturers)
          .catch(() => {});
      }
    } catch (err) {
      setError(parseError(err));
    }
  }

  useEffect(() => {
    if (!token) return;
    loadAll(token);
  }, [token]);

  async function doSync(label: string, fn: () => Promise<FlowOut>) {
    if (!token || !canEdit) return;
    setSyncing(label);
    setError('');
    setFeedback('');
    setLastFlow(null);
    try {
      const flow = await fn();
      setLastFlow(flow);
      setFeedback(flow.overall_success ? 'Sincronização concluída com sucesso.' : 'Sincronização concluída com erros — veja os detalhes abaixo.');
      await loadAll(token);
    } catch (err) {
      setError(parseError(err));
    } finally {
      setSyncing(null);
    }
  }

  async function syncFlow(trackerId: number) {
    await doSync(`flow-${trackerId}`, () =>
      apiFetch<FlowOut>(`/integrations/multiportal/trackers/${trackerId}/sync-flow`, { method: 'POST' }, token!),
    );
  }

  async function syncEquipment(trackerId: number) {
    await doSync(`equip-${trackerId}`, () =>
      apiFetch<FlowOut>(`/integrations/multiportal/trackers/${trackerId}/sync-equipment`, { method: 'POST' }, token!),
    );
  }

  async function queryLink(trackerId: number) {
    await doSync(`query-${trackerId}`, () =>
      apiFetch<FlowOut>(`/integrations/multiportal/trackers/${trackerId}/query-link`, {}, token!),
    );
  }

  async function syncChip() {
    if (!chipTrackerId) { setError('Selecione um rastreador para alterar o status do chip.'); return; }
    await doSync('chip', () =>
      apiFetch<FlowOut>(`/integrations/multiportal/trackers/${chipTrackerId}/sync-chip`, {
        method: 'POST',
        body: JSON.stringify({ chip_status: Number(chipStatus) }),
      }, token!),
    );
  }

  const filteredLogs = logs.filter((l) => {
    if (logFilter === 'success') return l.success;
    if (logFilter === 'error') return !l.success;
    return true;
  });

  const logStats = {
    total: logs.length,
    ok: logs.filter((l) => l.success).length,
    err: logs.filter((l) => !l.success).length,
  };

  const activeTrackers = trackers.filter((t) => t.integration_status === 'sincronizado').length;
  const errorTrackers = trackers.filter((t) => t.integration_status === 'erro').length;

  return (
    <PageShell title="Integração Multiportal" description="Sincronização de clientes, veículos e equipamentos com a plataforma Multiportal via Web Service.">
      {(error || feedback) && (
        <div className="mb-4 space-y-2">
          {error && <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Status da integração"
          value={status?.enabled ? 'Ativa' : 'Inativa'}
          hint={status?.credentials_configured ? 'Credenciais configuradas' : 'Credenciais pendentes'}
          tone={status?.enabled ? 'success' : 'warning'}
          icon={<PlugZap className="h-5 w-5" />}
        />
        <StatCard label="Logs registrados" value={logStats.total} hint={`${logStats.ok} ok · ${logStats.err} erros`} icon={<RefreshCw className="h-5 w-5" />} />
        <StatCard label="Sincronizados" value={activeTrackers} hint="Rastreadores com sync ok" tone="success" icon={<CheckCircle2 className="h-5 w-5" />} />
        <StatCard label="Com erro" value={errorTrackers} hint="Requerem reprocessamento" tone="warning" icon={<AlertTriangle className="h-5 w-5" />} />
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <SectionHeader eyebrow="Ações" title="Sincronização por rastreador" description="Execute o fluxo completo (cliente → veículo → equipamento → vínculo) ou só o equipamento isoladamente." />
          <div className="mt-5 space-y-3 max-h-[420px] overflow-y-auto pr-1">
            {trackers.length === 0 ? (
              <p className="text-sm text-slate-500">Nenhum rastreador cadastrado.</p>
            ) : trackers.map((t) => (
              <div key={t.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-slate-900 dark:text-white">{t.imei}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{[t.brand, t.model].filter(Boolean).join(' · ') || '-'} · {t.client_name || 'Sem cliente'} · {t.vehicle_plate || 'Sem veículo'}</p>
                  </div>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${t.integration_status === 'sincronizado' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : t.integration_status === 'erro' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-slate-200 bg-white text-slate-500'}`}>
                    {t.integration_status || 'sem sync'}
                  </span>
                </div>
                {canEdit && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={!!syncing}
                      onClick={() => syncFlow(t.id)}
                      className="rounded-2xl border border-brand-600 bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
                    >
                      {syncing === `flow-${t.id}` ? 'Sincronizando...' : 'Sync completo'}
                    </button>
                    <button
                      type="button"
                      disabled={!!syncing}
                      onClick={() => syncEquipment(t.id)}
                      className="rounded-2xl border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200"
                    >
                      {syncing === `equip-${t.id}` ? 'Aguarde...' : 'Só equipamento'}
                    </button>
                    <button
                      type="button"
                      disabled={!!syncing}
                      onClick={() => queryLink(t.id)}
                      className="rounded-2xl border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-200"
                    >
                      {syncing === `query-${t.id}` ? 'Consultando...' : 'Consultar vínculo'}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>

        <div className="space-y-6">
          {lastFlow && (
            <Card>
              <SectionHeader eyebrow="Resultado" title={lastFlow.overall_success ? 'Sincronização concluída' : 'Erro na sincronização'} description={`${lastFlow.entity_type} #${lastFlow.entity_id} — ${lastFlow.steps.length} etapa(s)`} />
              <div className="mt-4 space-y-2">
                {lastFlow.steps.map((step, i) => <StepCard key={i} step={step} />)}
              </div>
            </Card>
          )}

          {canEdit && (
            <Card>
              <SectionHeader eyebrow="Chip" title="Alterar status do SIM Card" description="Ativa, bloqueia, cancela ou suspende o chip via Multiportal." />
              <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto_auto]">
                <TrackerAutocomplete
                  trackers={trackers}
                  value={chipTrackerId}
                  onChange={setChipTrackerId}
                  placeholder="Buscar rastreador por IMEI, marca ou modelo…"
                />
                <select className={fieldClass} value={chipStatus} onChange={(e) => setChipStatus(e.target.value)}>
                  <option value="1">Ativo</option>
                  <option value="2">Bloqueado</option>
                  <option value="3">Cancelado</option>
                  <option value="4">Suspenso</option>
                </select>
                <Button type="button" disabled={!!syncing} onClick={syncChip}>{syncing === 'chip' ? 'Enviando...' : 'Alterar'}</Button>
              </div>
            </Card>
          )}

          <Card>
            <SectionHeader eyebrow="Domínio" title="Fabricantes disponíveis" description="Lista retornada pela Multiportal. Vincule aos rastreadores para habilitar o sync." />
            <div className="mt-4 space-y-2 max-h-[220px] overflow-y-auto pr-1">
              {manufacturers.length === 0 ? (
                <p className="text-sm text-slate-500">{status?.enabled ? 'Carregando...' : 'Ative a integração para consultar.'}</p>
              ) : manufacturers.map((m) => (
                <div key={m.code} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-950/60">
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">{m.description}</p>
                  <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs font-bold text-slate-500">ID {m.code}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </section>

      <section className="mt-6">
        <Card>
          <SectionHeader
            eyebrow="Auditoria"
            title="Logs de sincronização"
            description="Histórico de todas as chamadas ao Web Service Multiportal."
            actions={
              <div className="flex gap-2">
                {(['all', 'success', 'error'] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setLogFilter(f)}
                    className={`rounded-2xl border px-3 py-1.5 text-xs font-semibold transition ${logFilter === f ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-200 text-slate-600 hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-300'}`}
                  >
                    {f === 'all' ? 'Todos' : f === 'success' ? 'Sucesso' : 'Erros'}
                  </button>
                ))}
                <button type="button" onClick={() => token && loadAll(token)} className="rounded-2xl border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-brand-500 hover:text-brand-600 dark:border-slate-700 dark:text-slate-300">
                  Atualizar
                </button>
              </div>
            }
          />
          <div className="mt-5 space-y-2">
            {filteredLogs.length === 0 ? (
              <p className="text-sm text-slate-500">Nenhum log encontrado com o filtro selecionado.</p>
            ) : filteredLogs.map((entry) => {
              const expanded = expandedLogs.has(entry.id);
              return (
                <div key={entry.id} className="rounded-[24px] border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/60">
                  <button
                    type="button"
                    className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left"
                    onClick={() => setExpandedLogs((prev) => { const next = new Set(prev); expanded ? next.delete(entry.id) : next.add(entry.id); return next; })}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${severityClass(entry.severity, entry.success)}`}>{entry.success ? 'OK' : 'ERRO'}</span>
                        <p className="text-sm font-semibold text-slate-900 dark:text-white"><OperationLabel op={entry.operation} /></p>
                        <span className="text-xs text-slate-400">·</span>
                        <p className="text-xs text-slate-500">{entry.entity_type} #{entry.entity_id ?? '-'}</p>
                        {entry.response_code && <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-mono text-slate-500">{entry.response_code}</span>}
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">{entry.friendly_title || entry.response_description || '-'}</p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <p className="text-[10px] text-slate-400">{entry.created_at ? new Date(entry.created_at).toLocaleString('pt-BR') : '-'}</p>
                      {expanded ? <ChevronUp className="h-3.5 w-3.5 text-slate-400" /> : <ChevronDown className="h-3.5 w-3.5 text-slate-400" />}
                    </div>
                  </button>
                  {expanded && (
                    <div className="border-t border-slate-200 px-4 py-3 dark:border-slate-700">
                      <p className="text-xs text-slate-600 dark:text-slate-300">{entry.friendly_message || entry.response_description || 'Sem mensagem adicional.'}</p>
                      {entry.batch_id && <p className="mt-1 font-mono text-[10px] text-slate-400">batch: {entry.batch_id}</p>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      </section>
    </PageShell>
  );
}
