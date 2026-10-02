import { useCallback, useEffect, useMemo, useState } from 'react';
import { Coins } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { ClientAutocomplete } from '@/components/ui/client-autocomplete';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import type { IntervContract } from './types';

type PayerOption = { id: number; name: string; cpf_cnpj?: string; trade_name?: string | null };

export function IntervenienteModal({
  open,
  clientId,
  clientName,
  loading,
  error,
  ownedContracts,
  responsibleContracts,
  searchClients,
  onSave,
  onClose,
}: {
  open: boolean;
  clientId?: number;
  clientName?: string;
  loading: boolean;
  error?: string;
  ownedContracts: IntervContract[];
  responsibleContracts: IntervContract[];
  searchClients: (term: string) => Promise<PayerOption[]>;
  onSave: (contractId: number, payerId: number | null) => Promise<void>;
  onClose: () => void;
}) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [payerId, setPayerId] = useState('');
  const [payerOptions, setPayerOptions] = useState<PayerOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    setEditingId(null);
    setPayerId('');
    setPayerOptions([]);
    setSaveError('');
    setFeedback('');
  }, [clientId, open]);

  const searchAndRemember = useCallback(async (term: string) => {
    const results = await searchClients(term);
    setPayerOptions(current => {
      const known = new Set(current.map(client => client.id));
      return [...current, ...results.filter(client => !known.has(client.id))];
    });
    return results.filter(client => client.id !== clientId);
  }, [clientId, searchClients]);

  const choices = useMemo(() => {
    const values = new Map<number, PayerOption>();
    ownedContracts.forEach(contract => {
      if (contract.interveniente_client_id && contract.interveniente_name) {
        values.set(contract.interveniente_client_id, {
          id: contract.interveniente_client_id,
          name: contract.interveniente_name,
        });
      }
    });
    payerOptions.forEach(client => values.set(client.id, client));
    return [...values.values()].filter(client => client.id !== clientId);
  }, [clientId, ownedContracts, payerOptions]);

  const editingContract = ownedContracts.find(contract => contract.id === editingId);

  async function savePayer() {
    if (!editingContract) return;
    setSaveError('');
    setFeedback('');
    setSaving(true);
    try {
      await onSave(editingContract.id, payerId ? Number(payerId) : null);
      setFeedback(`Interveniente do contrato #${editingContract.id} atualizado.`);
      setEditingId(null);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Não foi possível salvar o interveniente.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={clientName ? `Interveniente financeiro — ${clientName}` : 'Interveniente financeiro'}
      size="2xl"
    >
      {loading ? (
        <TableSkeleton rows={4} cols={5} />
      ) : error ? (
        <p role="alert" className="text-sm text-rose-700 dark:text-rose-400">{error}</p>
      ) : (
        <div className="space-y-7">
          <section className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Contratos deste cliente e quem paga</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">Altere o interveniente de cada placa antes de gerar as cobranças.</p>
            </div>
            {ownedContracts.length === 0 ? (
              <EmptyState icon={Coins} title="Nenhum contrato deste cliente" description="Não há contratos vinculados a este cadastro." />
            ) : (
              <Table>
                <TableHead>
                  <Th>Contrato</Th>
                  <Th>Placa</Th>
                  <Th>Interveniente que paga</Th>
                  <Th>Situação</Th>
                  <Th>Ação</Th>
                </TableHead>
                <TableBody>
                  {ownedContracts.map(contract => (
                    <Tr key={contract.id}>
                      <Td className="text-xs text-slate-500">#{contract.id}</Td>
                      <Td className="font-mono font-semibold">{contract.vehicle_plate ?? '—'}</Td>
                      <Td className="text-sm">
                        {contract.interveniente_client_id
                          ? contract.interveniente_name || `Cadastro #${contract.interveniente_client_id} indisponível`
                          : 'O próprio cliente'}
                      </Td>
                      <Td><Badge variant={statusVariant(contract.status)}>{statusLabel(contract.status)}</Badge></Td>
                      <Td>
                        <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => {
                          setEditingId(contract.id);
                          setPayerId(String(contract.interveniente_client_id || ''));
                          setSaveError('');
                          setFeedback('');
                        }}>Alterar</Button>
                      </Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
            {editingContract && (
              <div className="space-y-3 rounded-xl border border-brand-300 bg-brand-50/40 p-4 dark:border-brand-700 dark:bg-brand-950/20">
                <p className="text-sm font-medium text-slate-900 dark:text-white">
                  Interveniente da placa {editingContract.vehicle_plate || `contrato #${editingContract.id}`}
                </p>
                <ClientAutocomplete
                  clients={choices}
                  value={payerId}
                  onChange={setPayerId}
                  searchClients={searchAndRemember}
                  placeholder="Buscar por nome, nome fantasia ou CPF/CNPJ"
                  disabled={saving}
                />
                <p className="text-xs text-slate-500 dark:text-slate-400">Deixe vazio se o próprio cliente paga. Títulos já gerados mantêm o pagador registrado na geração.</p>
                {saveError && <p role="alert" className="text-sm text-rose-700 dark:text-rose-400">{saveError}</p>}
                <div className="flex gap-2">
                  <Button onClick={savePayer} disabled={saving}>{saving ? 'Salvando...' : 'Salvar interveniente'}</Button>
                  <Button variant="secondary" onClick={() => setEditingId(null)} disabled={saving}>Cancelar</Button>
                </div>
              </div>
            )}
            {feedback && <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">{feedback}</p>}
            <p className="text-xs text-slate-500">Mostrando {ownedContracts.length} contrato(s) deste cliente</p>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-5 dark:border-slate-700">
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Contratos de outros clientes pagos por este cliente</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">Aqui aparece quando {clientName || 'este cliente'} é o interveniente financeiro.</p>
            </div>
            {responsibleContracts.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Nenhum contrato de outro cliente é pago por este cliente.</p>
            ) : (
              <Table>
                <TableHead>
                  <Th>Contrato</Th>
                  <Th>Placa</Th>
                  <Th>Cliente titular</Th>
                  <Th>Plano</Th>
                  <Th>Situação</Th>
                </TableHead>
                <TableBody>
                  {responsibleContracts.map(contract => (
                    <Tr key={contract.id}>
                      <Td className="text-xs text-slate-500">#{contract.id}</Td>
                      <Td className="font-mono font-semibold">{contract.vehicle_plate ?? '—'}</Td>
                      <Td className="text-sm">{contract.client_name ?? '—'}</Td>
                      <Td className="text-sm">{contract.plan_name ?? '—'}</Td>
                      <Td><Badge variant={statusVariant(contract.status)}>{statusLabel(contract.status)}</Badge></Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
            )}
            <p className="text-xs text-slate-500">Mostrando {responsibleContracts.length} vínculo(s) como interveniente</p>
          </section>
        </div>
      )}
    </Modal>
  );
}
