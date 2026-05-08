'use client';

import { useEffect, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type IntegrationStatus = {
  enabled: boolean;
  wsdl_url?: string | null;
  credentials_configured: boolean;
  group_codes_configured: boolean;
};

type Manufacturer = { code: string; description: string };
type IntegrationLog = {
  id: number;
  entity_type: string;
  entity_id?: number | null;
  operation: string;
  success: boolean;
  response_code?: string | null;
  response_description?: string | null;
  created_at?: string | null;
};

function parseError(error: unknown) {
  return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.';
}

export default function IntegracaoPage() {
  const { token } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([]);
  const [logs, setLogs] = useState<IntegrationLog[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!token) return;
    Promise.all([
      apiFetch<IntegrationStatus>('/integrations/multiportal/status', {}, token),
      apiFetch<IntegrationLog[]>('/integrations/multiportal/logs?limit=20', {}, token),
    ])
      .then(([statusResponse, logResponse]) => {
        setStatus(statusResponse);
        setLogs(logResponse);
        if (statusResponse.enabled) {
          apiFetch<Manufacturer[]>('/integrations/multiportal/manufacturers', {}, token)
            .then(setManufacturers)
            .catch((err) => setError(parseError(err)));
        }
      })
      .catch((err) => setError(parseError(err)));
  }, [token]);

  return (
    <PageShell title="Integração Multiportal">
      {error && <p className="mb-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}

      <section className="grid gap-4 lg:grid-cols-3">
        <Card>
          <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Status</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-900">{status?.enabled ? 'Ativada' : 'Desativada'}</h3>
          <p className="mt-2 text-sm text-slate-500">Use variáveis de ambiente para definir WSDL, credenciais e grupo de acesso.</p>
        </Card>
        <Card>
          <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Credenciais</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-900">{status?.credentials_configured ? 'Configuradas' : 'Pendentes'}</h3>
          <p className="mt-2 text-sm text-slate-500">Sem credenciais válidas, a sincronização fica bloqueada.</p>
        </Card>
        <Card>
          <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Grupo do portal</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-900">{status?.group_codes_configured ? 'Configurado' : 'Não definido'}</h3>
          <p className="mt-2 text-sm text-slate-500">Necessário para criação do usuário cliente no portal Multiportal.</p>
        </Card>
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Fabricantes</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-900">Domínio externo</h3>
          <div className="mt-5 space-y-3">
            {manufacturers.length === 0 ? <p className="text-sm text-slate-500">Sem fabricantes carregados. Ative a integração para consultar o domínio remoto.</p> : manufacturers.map((item) => (
              <div key={item.code} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                <p className="font-semibold text-slate-900">{item.code}</p>
                <p className="mt-1 text-slate-600">{item.description}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <p className="text-xs uppercase tracking-[0.22em] text-brand-500">Últimos logs</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-900">Auditoria da sincronização</h3>
          <div className="mt-5 space-y-3">
            {logs.length === 0 ? <p className="text-sm text-slate-500">Ainda não há execuções registradas.</p> : logs.map((entry) => (
              <div key={entry.id} className="rounded-3xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-slate-900">{entry.operation}</p>
                    <p className="mt-1 text-sm text-slate-500">{entry.entity_type} #{entry.entity_id || '-'} • {entry.created_at ? new Date(entry.created_at).toLocaleString('pt-BR') : '-'}</p>
                  </div>
                  <span className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase ${entry.success ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'}`}>{entry.success ? 'ok' : 'erro'}</span>
                </div>
                <p className="mt-3 text-sm text-slate-600">{entry.response_code || '-'} • {entry.response_description || 'Sem descrição'}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>
    </PageShell>
  );
}
