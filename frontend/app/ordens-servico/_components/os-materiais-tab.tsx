'use client';

import { useState } from 'react';
import { Pencil, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { apiFetch } from '@/lib/api';
import { ServiceOrderMaterial, fieldClass, parseError } from './types';

type MaterialFormState = { service_product_id: string; description: string; quantity: string; unit: string; unit_price: string };

const emptyForm: MaterialFormState = { service_product_id: '', description: '', quantity: '1', unit: '', unit_price: '' };

function toPayload(form: MaterialFormState) {
  return {
    service_product_id: form.service_product_id ? Number(form.service_product_id) : null,
    description: form.description.trim(),
    quantity: form.quantity || '1',
    unit: form.unit.trim() || null,
    unit_price: form.unit_price ? form.unit_price : null,
  };
}

export function OsMateriaisTab({
  orderId, materials, canEdit, token, serviceProducts, onError, onFeedback, onRefresh,
}: {
  orderId: number;
  materials: ServiceOrderMaterial[];
  canEdit: boolean;
  token: string;
  serviceProducts: { id: number; name: string }[];
  onError: (msg: string) => void;
  onFeedback: (msg: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [form, setForm] = useState<MaterialFormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<MaterialFormState>(emptyForm);

  async function create() {
    if (!form.description.trim()) return;
    setSaving(true);
    onError('');
    try {
      await apiFetch(`/service-orders/${orderId}/materials`, { method: 'POST', body: JSON.stringify(toPayload(form)) }, token);
      onFeedback('Material adicionado.');
      setForm(emptyForm);
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  function startEdit(material: ServiceOrderMaterial) {
    setEditingId(material.id);
    setEditForm({
      service_product_id: material.service_product_id ? String(material.service_product_id) : '',
      description: material.description,
      quantity: material.quantity,
      unit: material.unit ?? '',
      unit_price: material.unit_price ?? '',
    });
  }

  async function saveEdit(materialId: number) {
    setSaving(true);
    onError('');
    try {
      await apiFetch(`/service-orders/${orderId}/materials/${materialId}`, { method: 'PUT', body: JSON.stringify(toPayload(editForm)) }, token);
      onFeedback('Material atualizado.');
      setEditingId(null);
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove(materialId: number) {
    if (!window.confirm('Remover este material?')) return;
    onError('');
    try {
      await apiFetch(`/service-orders/${orderId}/materials/${materialId}`, { method: 'DELETE' }, token);
      onFeedback('Material removido.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    }
  }

  return (
    <div className="space-y-4">
      {canEdit && (
        <div className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50 sm:grid-cols-[1fr_auto_auto_auto_auto]">
          {serviceProducts.length > 0 && (
            <select
              className={fieldClass}
              value={form.service_product_id}
              onChange={(e) => {
                const product = serviceProducts.find((p) => String(p.id) === e.target.value);
                setForm((prev) => ({ ...prev, service_product_id: e.target.value, description: product ? product.name : prev.description }));
              }}
            >
              <option value="">Descrição livre</option>
              {serviceProducts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
          <input className={fieldClass} placeholder="Descrição" value={form.description} onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))} />
          <input className={fieldClass} style={{ width: 80 }} placeholder="Qtd." value={form.quantity} onChange={(e) => setForm((prev) => ({ ...prev, quantity: e.target.value }))} />
          <input className={fieldClass} style={{ width: 80 }} placeholder="Unid." value={form.unit} onChange={(e) => setForm((prev) => ({ ...prev, unit: e.target.value }))} />
          <input className={fieldClass} style={{ width: 110 }} placeholder="Preço unit." value={form.unit_price} onChange={(e) => setForm((prev) => ({ ...prev, unit_price: e.target.value }))} />
          <Button onClick={create} disabled={saving || !form.description.trim()} className="sm:col-span-5 sm:w-fit">Adicionar material</Button>
        </div>
      )}

      {materials.length === 0 ? (
        <EmptyState title="Nenhum material registrado" description="Adicione as peças e materiais usados na execução." />
      ) : (
        <Table>
          <TableHead>
            <Th>Descrição</Th>
            <Th>Qtd.</Th>
            <Th>Unid.</Th>
            <Th>Preço unit.</Th>
            {canEdit && <Th className="w-24" />}
          </TableHead>
          <TableBody>
            {materials.map((material) => (
              <Tr key={material.id}>
                {editingId === material.id ? (
                  <>
                    <Td><input className={fieldClass} value={editForm.description} onChange={(e) => setEditForm((prev) => ({ ...prev, description: e.target.value }))} /></Td>
                    <Td><input className={fieldClass} style={{ width: 70 }} value={editForm.quantity} onChange={(e) => setEditForm((prev) => ({ ...prev, quantity: e.target.value }))} /></Td>
                    <Td><input className={fieldClass} style={{ width: 70 }} value={editForm.unit} onChange={(e) => setEditForm((prev) => ({ ...prev, unit: e.target.value }))} /></Td>
                    <Td><input className={fieldClass} style={{ width: 90 }} value={editForm.unit_price} onChange={(e) => setEditForm((prev) => ({ ...prev, unit_price: e.target.value }))} /></Td>
                    <Td>
                      <div className="flex gap-1.5">
                        <Button onClick={() => saveEdit(material.id)} disabled={saving} className="px-2.5 py-1 text-xs">Salvar</Button>
                        <Button variant="secondary" onClick={() => setEditingId(null)} className="px-2.5 py-1 text-xs">Cancelar</Button>
                      </div>
                    </Td>
                  </>
                ) : (
                  <>
                    <Td>
                      <p className="text-sm">{material.description}</p>
                      {material.service_product_name && <p className="text-xs text-slate-500">Catálogo: {material.service_product_name}</p>}
                    </Td>
                    <Td className="text-sm">{material.quantity}</Td>
                    <Td className="text-sm">{material.unit ?? '—'}</Td>
                    <Td className="text-sm">{material.unit_price ? `R$ ${Number(material.unit_price).toFixed(2)}` : '—'}</Td>
                    {canEdit && (
                      <Td>
                        <div className="flex justify-end gap-1.5">
                          <button type="button" onClick={() => startEdit(material)} className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button type="button" onClick={() => remove(material.id)} className="rounded-lg border border-rose-200 bg-rose-50 p-1.5 text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </Td>
                    )}
                  </>
                )}
              </Tr>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
