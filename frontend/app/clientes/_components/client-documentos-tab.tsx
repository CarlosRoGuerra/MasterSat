import { FileText } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/empty-state';
import { documentCategoryOptions, envioMeta, fileInputClass } from './helpers';
import type { ClientDocument } from './types';

export function ClientDocumentosTab({
  canEdit,
  docCategory,
  onDocCategoryChange,
  onDocFilesChange,
  uploading,
  hasFilesSelected,
  onUpload,
  documents,
  onReview,
  onDelete,
}: {
  canEdit: boolean;
  docCategory: string;
  onDocCategoryChange: (category: string) => void;
  onDocFilesChange: (files: File[]) => void;
  uploading: boolean;
  hasFilesSelected: boolean;
  onUpload: () => void;
  documents: ClientDocument[];
  onReview: (documentId: number, status: 'aprovado' | 'reenvio_solicitado') => void;
  onDelete: (documentId: number) => void;
}) {
  return (
    <div className="space-y-4">
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Select value={docCategory} onChange={(e) => onDocCategoryChange(e.target.value)} className="w-44">
            {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
          </Select>
          <input type="file" multiple className={fileInputClass} onChange={(e) => onDocFilesChange(Array.from(e.target.files || []))} />
          <Button type="button" disabled={uploading || !hasFilesSelected} onClick={onUpload}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
        </div>
      )}
      {documents.length === 0 ? (
        <EmptyState icon={FileText} title="Nenhum documento" description="Nenhum documento foi anexado a este cliente." />
      ) : (
        <div className="space-y-3">
          {documents.map((doc) => (
            <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                  <p className="mt-0.5 text-xs text-slate-400">Categoria: {doc.category}</p>
                  {envioMeta(doc) && <p className="mt-0.5 text-xs text-slate-400">{envioMeta(doc)}</p>}
                  {doc.review_notes && <p className="mt-0.5 text-xs text-slate-400">Obs.: {doc.review_notes}</p>}
                </div>
                <Badge variant={statusVariant(doc.review_status)}>{statusLabel(doc.review_status)}</Badge>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Visualizar</a>
                <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Baixar</a>
                {canEdit && (
                  <>
                    <button type="button" onClick={() => onReview(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                    <button type="button" onClick={() => onReview(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                    <button type="button" onClick={() => onDelete(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
