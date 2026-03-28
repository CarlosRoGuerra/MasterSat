'use client';

import { useEffect, useState } from 'react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

type DashboardData = {
  clients: { active: number; inactive: number; delinquent: number };
  vehicles: { total: number };
  trackers: { installed: number; stock: number; maintenance: number };
  service_orders: { open: number; in_progress: number; completed: number };
  finance: { pending_count: number; overdue_count: number; received_month: number };
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState('');
  const { token, loading, error: guardError } = useAuthGuard(['admin', 'operacional', 'financeiro'], '/login/admin');

  useEffect(() => {
    if (!token) return;
    apiFetch<DashboardData>('/dashboard', {}, token)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Não foi possível carregar o dashboard.'));
  }, [token]);

  return (
    <PageShell title="Dashboard">
      {(guardError || error) && <p className="mb-4 text-sm text-red-600">{guardError || error}</p>}
      {loading && <p className="mb-4 text-sm text-slate-500">Validando sessão...</p>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <p className="text-sm text-slate-500">Clientes Ativos</p>
          <p className="text-3xl font-bold">{data?.clients.active ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Veículos</p>
          <p className="text-3xl font-bold">{data?.vehicles.total ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Rastreadores Instalados</p>
          <p className="text-3xl font-bold">{data?.trackers.installed ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Recebido</p>
          <p className="text-3xl font-bold">R$ {data?.finance.received_month?.toFixed(2) ?? '--'}</p>
        </Card>
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Card>
          <h3 className="mb-3 text-lg font-semibold">Clientes</h3>
          <p>Ativos: {data?.clients.active ?? '--'}</p>
          <p>Inativos: {data?.clients.inactive ?? '--'}</p>
          <p>Inadimplentes: {data?.clients.delinquent ?? '--'}</p>
        </Card>
        <Card>
          <h3 className="mb-3 text-lg font-semibold">Ordens de Serviço</h3>
          <p>Abertas: {data?.service_orders.open ?? '--'}</p>
          <p>Em andamento: {data?.service_orders.in_progress ?? '--'}</p>
          <p>Concluídas: {data?.service_orders.completed ?? '--'}</p>
        </Card>
        <Card>
          <h3 className="mb-3 text-lg font-semibold">Financeiro</h3>
          <p>Pendentes: {data?.finance.pending_count ?? '--'}</p>
          <p>Vencidas: {data?.finance.overdue_count ?? '--'}</p>
        </Card>
      </div>
    </PageShell>
  );
}
