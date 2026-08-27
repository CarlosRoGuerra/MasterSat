import { Coins } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import type { IntervContract } from './types';

export function IntervenienteModal({
  open,
  clientName,
  loading,
  contracts,
  onClose,
}: {
  open: boolean;
  clientName?: string;
  loading: boolean;
  contracts: IntervContract[];
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={clientName ? `Interveniente financeiro — ${clientName}` : 'Interveniente financeiro'}
      size="2xl"
    >
      {loading ? (
        <TableSkeleton rows={4} cols={5} />
      ) : contracts.length === 0 ? (
        <EmptyState
          icon={Coins}
          title="Nenhum vínculo como interveniente"
          description="Este cliente não responde pela cobrança de contratos de outros clientes."
        />
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
            {contracts.map((c) => (
              <Tr key={c.id}>
                <Td className="text-xs text-slate-500">#{c.id}</Td>
                <Td className="font-mono font-semibold">{c.vehicle_plate ?? '—'}</Td>
                <Td className="text-sm">{c.client_name ?? '—'}</Td>
                <Td className="text-sm">{c.plan_name ?? '—'}</Td>
                <Td><Badge variant={statusVariant(c.status)}>{statusLabel(c.status)}</Badge></Td>
              </Tr>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="mt-3 text-xs text-slate-400">
        Mostrando {contracts.length} registro(s)
      </p>
    </Modal>
  );
}
