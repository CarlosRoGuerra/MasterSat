import { Flag } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import type { BillingChange, BillingItem } from './types';

export function BillingHistoryModal({
  billing,
  loading,
  changes,
  onClose,
}: {
  billing: BillingItem | null;
  loading: boolean;
  changes: BillingChange[];
  onClose: () => void;
}) {
  return (
    <Modal
      open={!!billing}
      onClose={onClose}
      title={billing ? `Histórico de operações — boleto #${billing.id}` : 'Histórico'}
      size="xl"
    >
      {loading ? (
        <TableSkeleton rows={3} cols={5} />
      ) : changes.length === 0 ? (
        <EmptyState
          icon={Flag}
          title="Sem alterações registradas"
          description="Este boleto não teve valor ou vencimento alterados."
        />
      ) : (
        <Table>
          <TableHead>
            <Th>Data</Th>
            <Th>Campo</Th>
            <Th>De</Th>
            <Th>Para</Th>
            <Th>Justificativa</Th>
          </TableHead>
          <TableBody>
            {changes.map((ch) => (
              <Tr key={ch.id}>
                <Td className="text-xs">{ch.created_at ? new Date(ch.created_at).toLocaleString('pt-BR') : '—'}</Td>
                <Td className="text-xs font-medium">
                  {ch.field_name === 'amount' ? 'Valor' : ch.field_name === 'due_date' ? 'Vencimento' : ch.field_name}
                </Td>
                <Td className="font-mono text-xs">{ch.previous_value ?? '—'}</Td>
                <Td className="font-mono text-xs">{ch.new_value ?? '—'}</Td>
                <Td className="text-xs">{ch.justification}</Td>
              </Tr>
            ))}
          </TableBody>
        </Table>
      )}
    </Modal>
  );
}
