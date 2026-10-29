'use client';

import { useRef, useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { FormField, FormGrid } from '@/components/ui/form-field';
import { Input, Textarea } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { apiFetch } from '@/lib/api';

export type ManualChargeTarget = {
  trackerId: number;
  vehicleId: number;
  clientId: number;
  contractId: number | null;
  clientName: string;
  vehiclePlate: string;
  identifier: string;
};

export function ManualChargeModal({ target, token, onClose, onCreated }: {
  target: ManualChargeTarget;
  token: string;
  onClose: () => void;
  onCreated: (chargeItemId: number) => void;
}) {
  const [form, setForm] = useState({ title: '', amount: '', start_date: '', notes: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const sending = useRef(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) return;
    const normalizedAmount = form.amount.trim().replace(',', '.');
    const amount = Number(normalizedAmount);
    if (form.title.trim().length < 2 || !/^\d+(\.\d{1,2})?$/.test(normalizedAmount)
      || !Number.isFinite(amount) || amount <= 0 || amount > 99999999.99 || !form.start_date) {
      setError('Informe a descrição com pelo menos dois caracteres, um valor maior que zero com até duas casas decimais e a data do serviço.');
      return;
    }
    sending.current = true;
    setSaving(true);
    setError('');
    try {
      const item = await apiFetch<{ id: number }>('/client-charge-items/', {
        method: 'POST',
        body: JSON.stringify({
          client_id: target.clientId,
          vehicle_id: target.vehicleId,
          tracker_id: target.trackerId,
          contract_id: target.contractId,
          title: form.title.trim(),
          quantity: 1,
          unit_price: amount,
          installment_count: 1,
          start_date: form.start_date,
          description: form.notes.trim() || null,
        }),
      }, token);
      onCreated(item.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível criar a cobrança.');
    } finally {
      sending.current = false;
      setSaving(false);
    }
  }

  return (
    <Modal open title="Adicionar cobrança manual" size="md" onClose={() => { if (!sending.current) onClose(); }}>
      <form className="space-y-4" onSubmit={save}>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          {target.clientName} · {target.vehiclePlate} · Rastreador {target.identifier}
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Adiciona o valor ao fechamento mensal do cliente. Informe a data em que o serviço foi realizado;
          o vencimento será definido no fechamento.
        </p>
        {error && <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
        <fieldset disabled={saving} className="space-y-4">
          <FormField label="Descrição" required>
            <Input minLength={2} maxLength={140} required placeholder="Ex.: instalação do rastreador" value={form.title}
              onChange={e => setForm(prev => ({ ...prev, title: e.target.value }))} />
          </FormField>
          <FormGrid>
            <FormField label="Valor (R$)" required>
              <Input inputMode="decimal" required placeholder="Ex.: 120,00" value={form.amount}
                onChange={e => setForm(prev => ({ ...prev, amount: e.target.value }))} />
            </FormField>
            <FormField label="Data do serviço" required>
              <Input type="date" required value={form.start_date}
                onChange={e => setForm(prev => ({ ...prev, start_date: e.target.value }))} />
            </FormField>
          </FormGrid>
          <FormField label="Observações">
            <Textarea value={form.notes} onChange={e => setForm(prev => ({ ...prev, notes: e.target.value }))} />
          </FormField>
        </fieldset>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={saving} onClick={onClose}>Cancelar</Button>
          <Button type="submit" disabled={saving}>{saving ? 'Criando…' : 'Adicionar ao fechamento'}</Button>
        </div>
      </form>
    </Modal>
  );
}
