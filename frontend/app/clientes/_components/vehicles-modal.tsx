import Link from 'next/link';
import { AlertTriangle, Car } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import type { VehicleDetailed } from './types';

export function VehiclesModal({
  open,
  clientName,
  loading,
  vehicles,
  error,
  onClose,
}: {
  open: boolean;
  clientName?: string;
  loading: boolean;
  vehicles: VehicleDetailed[];
  error?: string;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={clientName ? `Veículos vinculados ao cliente — ${clientName}` : 'Veículos vinculados ao cliente'}
      size="2xl"
    >
      {loading ? (
        <TableSkeleton rows={5} cols={7} />
      ) : error ? (
        <EmptyState icon={AlertTriangle} tone="warning" title="Não foi possível carregar os vínculos" description={error} />
      ) : vehicles.length === 0 ? (
        <EmptyState icon={Car} title="Nenhum veículo vinculado" description="Este cliente não possui veículos cadastrados." />
      ) : (
        <Table>
          <TableHead>
            <Th>Tipo</Th>
            <Th>Placa</Th>
            <Th>Marca</Th>
            <Th>Modelo</Th>
            <Th>Situação</Th>
            <Th>Tipo Equip.</Th>
            <Th>Modelo Equip.</Th>
            <Th>IMEI</Th>
          </TableHead>
          <TableBody>
            {vehicles.map((v) => (
              <Tr key={v.id}>
                <Td className="text-xs capitalize">{v.type ?? '—'}</Td>
                <Td className="font-mono font-semibold">{v.plate}</Td>
                <Td className="text-sm">{v.brand ?? '—'}</Td>
                <Td className="text-sm">{v.model ?? '—'}</Td>
                <Td><Badge variant={statusVariant(v.status)}>{statusLabel(v.status)}</Badge></Td>
                <Td className="text-xs text-slate-500">{v.tracker_plan ?? (v.tracker_imei ? 'BÁSICO' : '—')}</Td>
                <Td className="text-xs text-slate-500">{v.tracker_model ? `${v.tracker_brand ?? ''} ${v.tracker_model}`.trim() : '—'}</Td>
                <Td className="font-mono text-xs">
                  {v.tracker_imei ?? '—'}
                  {v.tracker_client_mismatch && <p className="mt-1 font-sans text-amber-700 dark:text-amber-400">
                    Cliente do rastreador diferente do proprietário.{' '}
                    <Link className="underline" href={`/veiculos?focus=${v.id}`}>Conferir vínculo</Link>
                  </p>}
                </Td>
              </Tr>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Mostrando {vehicles.length} registro(s)
      </p>
    </Modal>
  );
}
