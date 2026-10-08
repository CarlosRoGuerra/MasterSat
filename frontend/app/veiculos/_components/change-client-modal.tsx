'use client';

import { FormEvent, useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { Modal } from '@/components/ui/modal';
import { apiFetch } from '@/lib/api';

type ClientOption = { id: number; name: string; cpf_cnpj?: string };
type Vehicle = { id: number; client_id: number; plate: string };

export function ChangeClientModal<T extends Vehicle>({
  open, onClose, onSaved, vehicle, clients, token, canChangeOwner,
  initialIntervenienteId = null,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (vehicle: T) => Promise<void>;
  vehicle: T | null;
  clients: ClientOption[];
  token: string;
  canChangeOwner: boolean;
  // mixed = os contratos têm pagadores diferentes; exige escolha.
  initialIntervenienteId?: number | null | 'mixed';
}) {
  const [clientId, setClientId] = useState('');
  const [payerMode, setPayerMode] = useState<'client' | 'other' | ''>('client');
  const [payerId, setPayerId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || !vehicle) return;
    setClientId(String(vehicle.client_id));
    setPayerMode(initialIntervenienteId === 'mixed' ? '' : initialIntervenienteId ? 'other' : 'client');
    setPayerId(typeof initialIntervenienteId === 'number' ? String(initialIntervenienteId) : '');
    setError('');
  }, [open, vehicle, initialIntervenienteId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!vehicle || !clientId || !payerMode || (payerMode === 'other' && !payerId) || saving) return;
    setSaving(true);
    setError('');
    try {
      const saved = await apiFetch<T>(`/vehicles/${vehicle.id}/change-client`, {
        method: 'POST',
        body: JSON.stringify({
          client_id: Number(clientId),
          interveniente_client_id: payerMode === 'other' ? Number(payerId) : null,
          expected_client_id: vehicle.client_id,
        }),
      }, token);
      await onSaved(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível concluir a troca.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={() => { if (!saving) onClose(); }} title="Trocar cliente / pagador" subtitle={vehicle?.plate} size="lg">
      <form onSubmit={submit} className="space-y-5">
        {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
        <fieldset disabled={saving} className="space-y-2">
          <legend className="mb-2 text-sm font-semibold text-slate-900 dark:text-white">Cliente do veículo</legend>
          <ClientAutocomplete
            clients={clients}
            value={clientId}
            onChange={(id) => { setClientId(id); setPayerMode('client'); setPayerId(''); }}
            placeholder="Selecionar cliente"
            required
            disabled={saving || !canChangeOwner}
          />
          <p className="text-xs text-slate-500 dark:text-slate-400">O cliente será atualizado no veículo, nos rastreadores instalados e nos contratos ativos desta placa.</p>
        </fieldset>
        <fieldset disabled={saving} className="space-y-3">
          <legend className="mb-2 text-sm font-semibold text-slate-900 dark:text-white">Quem paga as novas cobranças?</legend>
          {initialIntervenienteId === 'mixed' && <p className="text-sm text-amber-700">Os contratos têm pagadores diferentes. Escolha o pagador que será aplicado a todos os contratos ativos desta placa.</p>}
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input type="radio" name="payerMode" checked={payerMode === 'client'} onChange={() => setPayerMode('client')} />
            O próprio cliente
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input type="radio" name="payerMode" checked={payerMode === 'other'} onChange={() => setPayerMode('other')} />
            Outro cliente como interveniente (pagador)
          </label>
          {payerMode === 'other' && <ClientAutocomplete
            clients={clients.filter((client) => String(client.id) !== clientId)}
            value={payerId}
            onChange={setPayerId}
            placeholder="Selecionar pagador"
            required
            disabled={saving}
          />}
        </fieldset>
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          A instalação, o plano e o vencimento permanecem. A troca vale para novas cobranças; títulos já gerados mantêm seu cliente e pagador. A operação é feita no sistema, sem consultar a Multiportal.
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>Cancelar</Button>
          <Button type="submit" disabled={saving || !clientId || !payerMode || (payerMode === 'other' && !payerId)}>{saving ? 'Salvando...' : 'Confirmar troca'}</Button>
        </div>
      </form>
    </Modal>
  );
}
