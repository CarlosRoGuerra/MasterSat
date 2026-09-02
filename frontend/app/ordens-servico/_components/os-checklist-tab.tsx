'use client';

import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { apiFetch } from '@/lib/api';
import { ChecklistItem, ServiceOrder, checklistTemplates, fieldClass, parseError } from './types';

export function OsChecklistTab({
  order, canEdit, token, onError, onFeedback, onRefresh,
}: {
  order: ServiceOrder;
  canEdit: boolean;
  token: string;
  onError: (msg: string) => void;
  onFeedback: (msg: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [items, setItems] = useState<ChecklistItem[]>(order.checklist && order.checklist.length ? order.checklist : []);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(items) !== JSON.stringify(order.checklist ?? []);

  function updateItem(index: number, patch: Partial<ChecklistItem>) {
    setItems((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  function removeItem(index: number) {
    setItems((prev) => prev.filter((_, i) => i !== index));
  }

  function addItem() {
    setItems((prev) => [...prev, { description: '', done: false, notes: '' }]);
  }

  function applyTemplate() {
    setItems(checklistTemplates[order.type].map((description) => ({ description, done: false, notes: '' })));
  }

  async function save() {
    setSaving(true);
    onError('');
    try {
      const cleaned = items.filter((item) => item.description.trim());
      await apiFetch(`/service-orders/${order.id}`, {
        method: 'PUT',
        body: JSON.stringify({ checklist: cleaned }),
      }, token);
      onFeedback('Checklist salvo com sucesso.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">Marque os itens concluídos e registre observações por item.</p>
        {canEdit && (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={applyTemplate} className="px-3 py-1.5 text-xs">Usar template do tipo</Button>
            <Button variant="secondary" onClick={addItem} className="gap-1 px-3 py-1.5 text-xs"><Plus className="h-3.5 w-3.5" /> Item</Button>
          </div>
        )}
      </div>

      {items.length === 0 ? (
        <EmptyState title="Sem itens no checklist" description="Adicione itens manualmente ou use o template do tipo de serviço." />
      ) : (
        <div className="space-y-2">
          {items.map((item, index) => (
            <div key={index} className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
              <input
                type="checkbox"
                checked={item.done}
                disabled={!canEdit}
                onChange={(e) => updateItem(index, { done: e.target.checked })}
                className="mt-1 h-4 w-4 rounded accent-brand-700"
              />
              <div className="flex-1 space-y-1.5">
                <input
                  className={fieldClass}
                  value={item.description}
                  disabled={!canEdit}
                  onChange={(e) => updateItem(index, { description: e.target.value })}
                  placeholder={`Item ${index + 1}`}
                />
                <input
                  className={`${fieldClass} text-xs`}
                  value={item.notes ?? ''}
                  disabled={!canEdit}
                  onChange={(e) => updateItem(index, { notes: e.target.value })}
                  placeholder="Observação (opcional)"
                />
              </div>
              {canEdit && (
                <button type="button" onClick={() => removeItem(index)} className="mt-1 rounded-lg border border-rose-200 bg-rose-50 p-1.5 text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {canEdit && (
        <div className="flex justify-end border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button onClick={save} disabled={!dirty || saving}>{saving ? 'Salvando…' : 'Salvar checklist'}</Button>
        </div>
      )}
    </div>
  );
}
