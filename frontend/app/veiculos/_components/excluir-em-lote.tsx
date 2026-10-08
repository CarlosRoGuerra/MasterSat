'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';

export type VehicleDeleteResult = {
  simulacao: boolean; total_enviados: number; aplicados: number; ignorados: number;
  itens: { vehicle_id: number; plate: string | null; situacao: 'aplicado' | 'ignorado'; motivo: string | null }[];
};

export function ExcluirVeiculosEmLote({ ids, onLimpar, onExecutar, onConcluido }: {
  ids: number[];
  onLimpar: () => void;
  onExecutar: (body: { ids: number[]; simular: boolean }) => Promise<VehicleDeleteResult>;
  onConcluido: (result: VehicleDeleteResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<VehicleDeleteResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const selection = ids.join(',');
  useEffect(() => {
    setPreview(null);
    setError('');
    if (!ids.length) { setOpen(false); return; }
    if (!open) return;
    let active = true;
    setChecking(true);
    onExecutar({ ids, simular: true })
      .then((result) => { if (active) setPreview(result); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Falha ao conferir seleção.'); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
    // A seleção determina a consulta; a função do pai pode mudar após a resposta.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, selection]);

  if (!ids.length) return null;
  async function apply() {
    if (!preview?.aplicados || applying) return;
    setApplying(true);
    setError('');
    try {
      const result = await onExecutar({ ids, simular: false });
      setOpen(false);
      onConcluido(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Falha ao excluir veículos.');
    } finally {
      setApplying(false);
    }
  }
  return <>
    <div className="mb-3 flex flex-wrap items-center gap-3 rounded-2xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm dark:border-brand-900/50 dark:bg-brand-950/30">
      <span>{ids.length} selecionado(s)</span>
      <Button variant="ghost" onClick={onLimpar} disabled={applying}>Limpar seleção</Button>
      <Button variant="danger" onClick={() => setOpen(true)}>Excluir selecionados</Button>
    </div>
    <Modal open={open} onClose={() => !applying && setOpen(false)} title="Excluir veículos" size="md"
      description="A exclusão remove o cadastro da lista e preserva o histórico. Veículos com rastreador vinculado ou contrato ativo ficam de fora."
      footer={<div className="flex justify-end gap-2">
        <Button variant="ghost" disabled={applying} onClick={() => setOpen(false)}>Cancelar</Button>
        <Button variant="danger" disabled={checking || applying || !preview?.aplicados} onClick={apply}>
          {applying ? 'Excluindo…' : `Excluir ${preview?.aplicados ?? 0} veículo(s)`}
        </Button>
      </div>}>
      {checking && <p>Conferindo a seleção…</p>}
      {error && <p role="alert" className="text-rose-700">{error}</p>}
      {preview && <div className="space-y-3">
        <p>{preview.aplicados} serão excluído(s). {preview.ignorados} ficam de fora.</p>
        <ul className="max-h-56 space-y-2 overflow-auto text-sm">
          {preview.itens.filter((item) => item.situacao === 'ignorado').map((item) =>
            <li key={item.vehicle_id}><strong>{item.plate ?? `#${item.vehicle_id}`}</strong>: {item.motivo}</li>)}
        </ul>
      </div>}
    </Modal>
  </>;
}
