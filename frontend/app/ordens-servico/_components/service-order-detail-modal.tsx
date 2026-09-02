'use client';

import { useEffect, useState } from 'react';

import { Modal } from '@/components/ui/modal';
import { apiFetch } from '@/lib/api';
import { DetailsTab, OrderDocument, OrderLog, ServiceOrder, ServiceOrderMaterial, TAB_LABEL, parseError } from './types';
import { OsDetalhesTab } from './os-detalhes-tab';
import { OsChecklistTab } from './os-checklist-tab';
import { OsMateriaisTab } from './os-materiais-tab';
import { OsFotosTab } from './os-fotos-tab';
import { OsAssinaturasTab } from './os-assinaturas-tab';
import { OsDocumentosTab } from './os-documentos-tab';
import { OsHistoricoTab } from './os-historico-tab';

const TABS: DetailsTab[] = ['detalhes', 'checklist', 'materiais', 'fotos', 'assinaturas', 'documentos', 'historico'];

export function ServiceOrderDetailModal({
  open, onClose, orderId, token, canEdit, onOrderChanged, serviceProducts,
}: {
  open: boolean;
  onClose: () => void;
  orderId: number | null;
  token: string;
  canEdit: boolean;
  /** Avisa o container (page.tsx) que algo mudou, pra ele atualizar a lista/stats. */
  onOrderChanged: () => void;
  serviceProducts: { id: number; name: string }[];
}) {
  const [tab, setTab] = useState<DetailsTab>('detalhes');
  const [order, setOrder] = useState<ServiceOrder | null>(null);
  const [logs, setLogs] = useState<OrderLog[]>([]);
  const [documents, setDocuments] = useState<OrderDocument[]>([]);
  const [materials, setMaterials] = useState<ServiceOrderMaterial[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  async function reload() {
    if (!orderId) return;
    setLoading(true);
    setError('');
    try {
      const [orderResp, logResp, docResp, matResp] = await Promise.all([
        apiFetch<ServiceOrder>(`/service-orders/${orderId}`, {}, token),
        apiFetch<OrderLog[]>(`/service-orders/${orderId}/logs`, {}, token),
        apiFetch<OrderDocument[]>(`/service-orders/${orderId}/documents`, {}, token),
        apiFetch<ServiceOrderMaterial[]>(`/service-orders/${orderId}/materials`, {}, token),
      ]);
      setOrder(orderResp);
      setLogs(logResp);
      setDocuments(docResp);
      setMaterials(matResp);
      onOrderChanged();
    } catch (err) {
      setError(parseError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open && orderId) {
      setTab('detalhes');
      setFeedback('');
      reload();
    }
    if (!open) {
      setOrder(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, orderId]);

  return (
    <Modal open={open} onClose={onClose} title={order?.number ?? ''} subtitle="Ordem de Serviço" size="xl">
      {loading && !order ? (
        <p className="text-sm text-slate-500">Carregando…</p>
      ) : order ? (
        <div className="space-y-4">
          {(error || feedback) && (
            <div className="space-y-2">
              {error && <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}
              {feedback && <p className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
            </div>
          )}

          <div className="flex flex-wrap gap-1 border-b border-slate-100 dark:border-slate-800">
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`px-3.5 py-2 text-sm font-medium transition-colors ${tab === t ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
              >
                {TAB_LABEL[t]}
              </button>
            ))}
          </div>

          {tab === 'detalhes' && (
            <OsDetalhesTab order={order} canEdit={canEdit} token={token} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'checklist' && (
            <OsChecklistTab order={order} canEdit={canEdit} token={token} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'materiais' && (
            <OsMateriaisTab orderId={order.id} materials={materials} canEdit={canEdit} token={token} serviceProducts={serviceProducts} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'fotos' && (
            <OsFotosTab orderId={order.id} documents={documents} canEdit={canEdit} token={token} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'assinaturas' && (
            <OsAssinaturasTab order={order} canEdit={canEdit} token={token} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'documentos' && (
            <OsDocumentosTab orderId={order.id} documents={documents} canEdit={canEdit} token={token} onError={setError} onFeedback={setFeedback} onRefresh={reload} />
          )}
          {tab === 'historico' && <OsHistoricoTab logs={logs} />}
        </div>
      ) : null}
    </Modal>
  );
}
