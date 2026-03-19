'use client';

import { useEffect, useState } from 'react';

import { ClientShell } from '@/components/client-shell';
import { Card } from '@/components/ui/card';
import { apiFetch } from '@/lib/api';
import { clearSession, getAccessToken } from '@/lib/auth';

type ClientDashboardData = {
  profile: {
    id: number;
    name: string;
    cpf_cnpj: string;
    email?: string | null;
    phone?: string | null;
    city?: string | null;
    state?: string | null;
    status: string;
  };
  summary: {
    total_vehicles: number;
    active_vehicles: number;
    pending_billings: number;
    overdue_billings: number;
    total_open_amount: number;
  };
  vehicles: Array<{
    id: number;
    plate: string;
    model?: string | null;
    brand?: string | null;
    year?: number | null;
    status: string;
  }>;
  recent_billings: Array<{
    id: number;
    amount: number;
    due_date: string;
    status: string;
    payment_date?: string | null;
    payment_method?: string | null;
  }>;
};

export default function ClientDashboardPage() {
  const [data, setData] = useState<ClientDashboardData | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      window.location.href = '/login/cliente';
      return;
    }
    apiFetch<ClientDashboardData>('/client-portal/dashboard', {}, token)
      .then(setData)
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Não foi possível carregar o portal do cliente.');
        if (err instanceof Error && err.message.includes('credenciais')) {
          clearSession();
          window.location.href = '/login/cliente';
        }
      });
  }, []);

  return (
    <ClientShell title="Dashboard do cliente">
      {error && <p className="mb-4 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <p className="text-sm text-slate-500">Veículos vinculados</p>
          <p className="text-3xl font-bold text-slate-900">{data?.summary.total_vehicles ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Veículos ativos</p>
          <p className="text-3xl font-bold text-slate-900">{data?.summary.active_vehicles ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Cobranças pendentes</p>
          <p className="text-3xl font-bold text-slate-900">{data?.summary.pending_billings ?? '--'}</p>
        </Card>
        <Card>
          <p className="text-sm text-slate-500">Valor em aberto</p>
          <p className="text-3xl font-bold text-slate-900">R$ {data?.summary.total_open_amount?.toFixed(2) ?? '--'}</p>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_1fr]">
        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Dados cadastrais</h3>
          <div className="space-y-2 text-sm text-slate-600">
            <p><span className="font-medium text-slate-900">Cliente:</span> {data?.profile.name ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Documento:</span> {data?.profile.cpf_cnpj ?? '--'}</p>
            <p><span className="font-medium text-slate-900">E-mail:</span> {data?.profile.email ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Telefone:</span> {data?.profile.phone ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Cidade:</span> {data?.profile.city ? `${data.profile.city}/${data.profile.state}` : '--'}</p>
            <p><span className="font-medium text-slate-900">Status:</span> {data?.profile.status ?? '--'}</p>
          </div>
        </Card>

        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Resumo financeiro</h3>
          <div className="space-y-2 text-sm text-slate-600">
            <p><span className="font-medium text-slate-900">Pendências:</span> {data?.summary.pending_billings ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Vencidas:</span> {data?.summary.overdue_billings ?? '--'}</p>
            <p><span className="font-medium text-slate-900">Total em aberto:</span> R$ {data?.summary.total_open_amount?.toFixed(2) ?? '--'}</p>
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Veículos vinculados</h3>
          <div className="space-y-3 text-sm text-slate-600">
            {data?.vehicles.length ? (
              data.vehicles.map((vehicle) => (
                <div key={vehicle.id} className="rounded-2xl border border-slate-200 p-4">
                  <p className="font-semibold text-slate-900">{vehicle.plate}</p>
                  <p>{vehicle.brand ?? 'Marca não informada'} • {vehicle.model ?? 'Modelo não informado'}</p>
                  <p>Ano: {vehicle.year ?? '--'} • Status: {vehicle.status}</p>
                </div>
              ))
            ) : (
              <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Nenhum veículo vinculado até o momento.</p>
            )}
          </div>
        </Card>

        <Card>
          <h3 className="mb-4 text-lg font-semibold text-slate-900">Cobranças recentes</h3>
          <div className="space-y-3 text-sm text-slate-600">
            {data?.recent_billings.length ? (
              data.recent_billings.map((billing) => (
                <div key={billing.id} className="rounded-2xl border border-slate-200 p-4">
                  <p className="font-semibold text-slate-900">R$ {billing.amount.toFixed(2)}</p>
                  <p>Vencimento: {new Date(`${billing.due_date}T00:00:00`).toLocaleDateString('pt-BR')}</p>
                  <p>Status: {billing.status}</p>
                  <p>Pagamento: {billing.payment_date ? new Date(`${billing.payment_date}T00:00:00`).toLocaleDateString('pt-BR') : '--'}</p>
                </div>
              ))
            ) : (
              <p className="rounded-2xl border border-dashed border-slate-300 p-4 text-slate-500">Nenhuma cobrança disponível.</p>
            )}
          </div>
        </Card>
      </div>
    </ClientShell>
  );
}
