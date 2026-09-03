import { Modal } from '@/components/ui/modal';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { ClientCadastroTab } from './client-cadastro-tab';
import { ClientHistoricoTab } from './client-historico-tab';
import { ClientDocumentosTab } from './client-documentos-tab';
import type { Client, ClientDocument, VehicleSummary } from './types';

export type DetailsTab = 'cadastro' | 'historico' | 'documentos';

const TAB_LABEL: Record<DetailsTab, string> = {
  cadastro: 'Cadastro',
  historico: 'Histórico',
  documentos: 'Documentos',
};

export function ClientDetailModal({
  open,
  client,
  vehicles,
  tab,
  onTabChange,
  onClose,
  token,
  canViewFinance,
  isAdmin,
  onExportTimelinePdf,
  onOpenBillings,
  canEdit,
  docCategory,
  onDocCategoryChange,
  onDocFilesChange,
  uploadingDocs,
  hasFilesSelected,
  onUploadDocs,
  documents,
  onReviewDocument,
  onDeleteDocument,
}: {
  open: boolean;
  client: Client | null;
  vehicles: VehicleSummary[];
  tab: DetailsTab;
  onTabChange: (tab: DetailsTab) => void;
  onClose: () => void;
  token: string;
  canViewFinance: boolean;
  isAdmin: boolean;
  onExportTimelinePdf: () => void;
  onOpenBillings: () => void;
  canEdit: boolean;
  docCategory: string;
  onDocCategoryChange: (category: string) => void;
  onDocFilesChange: (files: File[]) => void;
  uploadingDocs: boolean;
  hasFilesSelected: boolean;
  onUploadDocs: () => void;
  documents: ClientDocument[];
  onReviewDocument: (documentId: number, status: 'aprovado' | 'reenvio_solicitado') => void;
  onDeleteDocument: (documentId: number) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title={client?.name ?? ''} subtitle="Detalhes do cliente" size="xl">
      {client && (
        <div className="space-y-4">
          {/* Situação do cadastro + do contrato assinado, logo no topo */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={statusVariant(client.status)}>{statusLabel(client.status)}</Badge>
            {client.contrato_armazenado
              ? <Badge variant="success">Contrato armazenado</Badge>
              : <Badge variant="warning">Contrato pendente</Badge>}
          </div>

          {/* Abas */}
          <div className="flex gap-1 border-b border-slate-100 dark:border-slate-800">
            {(['cadastro', 'historico', 'documentos'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => onTabChange(t)}
                className={`px-4 py-2 text-sm font-medium transition-colors ${tab === t ? 'border-b-2 border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
              >
                {TAB_LABEL[t]}
              </button>
            ))}
          </div>

          {tab === 'cadastro' && <ClientCadastroTab client={client} vehicles={vehicles} />}

          {tab === 'historico' && client && (
            <ClientHistoricoTab
              clientId={client.id}
              token={token}
              canViewFinance={canViewFinance}
              isAdmin={isAdmin}
              onExportPdf={onExportTimelinePdf}
              onOpenBillings={onOpenBillings}
            />
          )}

          {tab === 'documentos' && (
            <ClientDocumentosTab
              canEdit={canEdit}
              docCategory={docCategory}
              onDocCategoryChange={onDocCategoryChange}
              onDocFilesChange={onDocFilesChange}
              uploading={uploadingDocs}
              hasFilesSelected={hasFilesSelected}
              onUpload={onUploadDocs}
              documents={documents}
              onReview={onReviewDocument}
              onDelete={onDeleteDocument}
            />
          )}
        </div>
      )}
    </Modal>
  );
}
