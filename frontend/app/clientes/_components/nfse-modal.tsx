import { FileText } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Badge } from '@/components/ui/badge';
import { EmptyState, TableSkeleton } from '@/components/ui/empty-state';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import type { NfseItem } from './types';

export function NfseModal({
  open,
  clientName,
  loading,
  notas,
  onClose,
  onVerPdf,
}: {
  open: boolean;
  clientName?: string;
  loading: boolean;
  notas: NfseItem[];
  onClose: () => void;
  onVerPdf: (billingId: number) => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={clientName ? `Notas fiscais — ${clientName}` : 'Notas fiscais'}
      size="2xl"
    >
      {loading ? (
        <TableSkeleton rows={4} cols={6} />
      ) : notas.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="Nenhuma nota fiscal"
          description="Este cliente ainda não possui NFS-e emitida. As notas são geradas a partir das cobranças no fechamento."
        />
      ) : (
        <Table>
          <TableHead>
            <Th>Nº NFS-e</Th>
            <Th>Cobrança</Th>
            <Th>Valor</Th>
            <Th>Emissão</Th>
            <Th>Situação</Th>
            <Th className="w-24" />
          </TableHead>
          <TableBody>
            {notas.map((n) => (
              <Tr key={n.billing_id}>
                <Td className="font-mono font-semibold">{n.numero_nfse ?? '—'}</Td>
                <Td className="text-xs">#{n.billing_id}{n.titulo ? ` · ${n.titulo}` : ''}</Td>
                <Td className="font-mono">
                  {n.valor != null ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n.valor) : '—'}
                </Td>
                <Td className="text-xs">{n.data_emissao ? new Date(n.data_emissao).toLocaleDateString('pt-BR') : '—'}</Td>
                <Td>
                  <Badge variant={n.status === 'emitida' ? 'success' : n.status === 'erro' ? 'danger' : 'warning'}>
                    {n.status === 'emitida' ? 'Emitida' : n.status === 'erro' ? 'Erro' : 'Processando'}
                  </Badge>
                </Td>
                <Td>
                  {/* O PDF vem primeiro: mandar o operador para a consulta do
                      governo para depois baixar de la era um desvio inutil. */}
                  <div className="flex items-center gap-1.5">
                    {n.status === 'emitida' && (
                      <button
                        type="button"
                        onClick={() => onVerPdf(n.billing_id)}
                        className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 text-[11px] font-semibold text-brand-700 transition hover:bg-brand-100 dark:border-brand-900/40 dark:bg-brand-950/30 dark:text-brand-400"
                      >
                        Ver PDF
                      </button>
                    )}
                    {n.link_visualizacao && (
                      <a
                        href={n.link_visualizacao}
                        target="_blank"
                        rel="noreferrer"
                        title="Consulta publica no portal da NFS-e"
                        className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                      >
                        Consulta
                      </a>
                    )}
                  </div>
                </Td>
              </Tr>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="mt-3 text-xs text-slate-400">
        Mostrando {notas.length} registro(s)
      </p>
    </Modal>
  );
}
