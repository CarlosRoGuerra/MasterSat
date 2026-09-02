'use client';

import { useState } from 'react';

import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import type { OrderStatus } from '@/lib/domain-types';
import {
  ServiceOrder, statusOptions, typeLabel, formatDateTimeLabel, areaClass, parseError,
} from './types';

export function OsDetalhesTab({
  order, canEdit, token, onError, onFeedback, onRefresh,
}: {
  order: ServiceOrder;
  canEdit: boolean;
  token: string;
  onError: (msg: string) => void;
  onFeedback: (msg: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [executionDescription, setExecutionDescription] = useState(order.execution_description || '');
  const [savingExecution, setSavingExecution] = useState(false);
  const [changingStatus, setChangingStatus] = useState(false);

  async function saveExecutionDescription() {
    setSavingExecution(true);
    onError('');
    try {
      await apiFetch(`/service-orders/${order.id}`, {
        method: 'PUT',
        body: JSON.stringify({ execution_description: executionDescription.trim() || null }),
      }, token);
      onFeedback('Descrição do serviço executado salva.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setSavingExecution(false);
    }
  }

  async function changeStatus(status: OrderStatus) {
    setChangingStatus(true);
    onError('');
    onFeedback('');
    const notes = window.prompt('Observações desta etapa (opcional):', '') || '';
    try {
      await apiFetch(`/service-orders/${order.id}/status`, {
        method: 'POST',
        body: JSON.stringify({ status, notes: notes || null }),
      }, token);
      onFeedback('Status atualizado com sucesso.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setChangingStatus(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {[
          ['Status', <Badge key="s" variant={statusVariant(order.status)}>{statusOptions.find((x) => x.value === order.status)?.label ?? order.status}</Badge>],
          ['Prioridade', <Badge key="p" variant={statusVariant(order.priority)}>{statusLabel(order.priority)}</Badge>],
          ['Tipo', typeLabel(order.type)],
          ['Cliente', order.client_name ?? '—'],
          ['Veículo / Rastreador', [order.vehicle_plate, order.tracker_label].filter(Boolean).join(' • ') || '—'],
          ['Técnico', order.technician_name ?? '—'],
          ['Agendado', formatDateTimeLabel(order.scheduled_at)],
          ['Executado', formatDateTimeLabel(order.executed_at)],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
            <p className="text-2xs font-semibold uppercase tracking-widest text-slate-500">{label}</p>
            <div className="mt-1 text-sm text-slate-800 dark:text-slate-200">{value}</div>
          </div>
        ))}
      </div>

      {order.problem_description && (
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="mb-1 text-2xs font-semibold uppercase tracking-widest text-slate-500">Descrição do problema</p>
          <p className="text-sm text-slate-700 dark:text-slate-300">{order.problem_description}</p>
        </div>
      )}

      {order.observations && (
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="mb-1 text-2xs font-semibold uppercase tracking-widest text-slate-500">Observações</p>
          <p className="text-sm text-slate-700 dark:text-slate-300">{order.observations}</p>
        </div>
      )}

      <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
        <p className="mb-2 text-2xs font-semibold uppercase tracking-widest text-slate-500">Descrição do serviço executado</p>
        <textarea
          className={areaClass}
          value={executionDescription}
          onChange={(e) => setExecutionDescription(e.target.value)}
          placeholder="O que foi feito em campo — exigido para concluir a OS"
          disabled={!canEdit}
        />
        {canEdit && (
          <div className="mt-2 flex justify-end">
            <Button
              variant="secondary"
              onClick={saveExecutionDescription}
              disabled={savingExecution || executionDescription.trim() === (order.execution_description || '')}
              className="px-3 py-1.5 text-xs"
            >
              {savingExecution ? 'Salvando…' : 'Salvar descrição'}
            </Button>
          </div>
        )}
      </div>

      {canEdit && (
        <div>
          <p className="mb-2 text-2xs font-semibold uppercase tracking-widest text-slate-500">Alterar status</p>
          <div className="flex flex-wrap gap-2">
            {statusOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                disabled={changingStatus}
                onClick={() => changeStatus(opt.value)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors border disabled:opacity-50 ${order.status === opt.value ? 'bg-brand-700 text-white border-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800'}`}
              >{opt.label}</button>
            ))}
          </div>
          {order.status !== 'concluida' && (
            <p className="mt-2 text-xs text-slate-500">
              Concluir exige descrição do serviço executado e as assinaturas do técnico e do cliente (aba Assinaturas).
            </p>
          )}
        </div>
      )}
    </div>
  );
}
