'use client';

import { useState } from 'react';
import { FileText, FileType } from 'lucide-react';

import { Badge, statusVariant, statusLabel } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { apiFetch } from '@/lib/api';
import type { DocumentReviewStatus as ReviewStatus } from '@/lib/domain-types';
import { OrderDocument, documentCategoryOptions, fieldClass, parseError, pdfKinds } from './types';

export function OsDocumentosTab({
  orderId, documents, canEdit, token, onError, onFeedback, onRefresh,
}: {
  orderId: number;
  documents: OrderDocument[];
  canEdit: boolean;
  token: string;
  onError: (msg: string) => void;
  onFeedback: (msg: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [format, setFormat] = useState<'pdf' | 'docx'>('pdf');
  const [generatingKind, setGeneratingKind] = useState<string | null>(null);
  const [docCategory, setDocCategory] = useState(documentCategoryOptions[0]);
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);

  const files = documents.filter((doc) => !(doc.content_type ?? '').startsWith('image/'));

  async function generate(kind: (typeof pdfKinds)[number]['value']) {
    setGeneratingKind(kind);
    onError('');
    try {
      await apiFetch(`/service-orders/${orderId}/generate-document`, { method: 'POST', body: JSON.stringify({ kind, format }) }, token);
      onFeedback(`Documento gerado em ${format.toUpperCase()} com sucesso.`);
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setGeneratingKind(null);
    }
  }

  async function uploadDocuments() {
    if (!docFiles.length) return;
    setUploading(true);
    onError('');
    try {
      const body = new FormData();
      body.append('category', docCategory);
      docFiles.forEach((file) => body.append('files', file));
      await apiFetch(`/service-orders/${orderId}/documents`, { method: 'POST', body }, token);
      onFeedback('Arquivos enviados com sucesso.');
      setDocFiles([]);
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  async function reviewDocument(documentId: number, status: ReviewStatus) {
    const notes = window.prompt('Observações da revisão (opcional):', '') || '';
    try {
      await apiFetch(`/service-orders/${orderId}/documents/${documentId}/review`, { method: 'PUT', body: JSON.stringify({ review_status: status, review_notes: notes || null }) }, token);
      onFeedback('Status do documento atualizado.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    }
  }

  async function deleteDocument(documentId: number) {
    if (!window.confirm('Deseja remover este documento?')) return;
    try {
      await apiFetch(`/service-orders/${orderId}/documents/${documentId}`, { method: 'DELETE' }, token);
      onFeedback('Documento removido com sucesso.');
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-bold uppercase tracking-widest text-slate-500">Gerar documento profissional</p>
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
            <button
              type="button"
              onClick={() => setFormat('pdf')}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ${format === 'pdf' ? 'bg-brand-700 text-white' : 'text-slate-500'}`}
            ><FileText className="h-3.5 w-3.5" /> PDF</button>
            <button
              type="button"
              onClick={() => setFormat('docx')}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ${format === 'docx' ? 'bg-brand-700 text-white' : 'text-slate-500'}`}
            ><FileType className="h-3.5 w-3.5" /> DOCX</button>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {pdfKinds.map((item) => (
            <Button key={item.value} variant="secondary" disabled={generatingKind === item.value} onClick={() => generate(item.value)} className="text-xs px-3 py-1.5">
              {generatingKind === item.value ? 'Gerando…' : item.label}
            </Button>
          ))}
        </div>
      </div>

      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <select className={fieldClass} style={{ width: 200 }} value={docCategory} onChange={(e) => setDocCategory(e.target.value)}>
            {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input type="file" multiple className={`${fieldClass} file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:text-white`} onChange={(e) => setDocFiles(Array.from(e.target.files || []))} />
          <Button disabled={!docFiles.length || uploading} onClick={uploadDocuments}>{uploading ? 'Enviando…' : 'Enviar'}</Button>
        </div>
      )}

      {files.length === 0 ? (
        <EmptyState title="Sem documentos" description="Nenhum documento vinculado a esta OS." />
      ) : (
        <div className="space-y-2">
          {files.map((doc) => (
            <div key={doc.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">{doc.file_name}</p>
                  <p className="text-xs text-slate-500">{doc.category}</p>
                </div>
                <Badge variant={statusVariant(doc.review_status)}>{statusLabel(doc.review_status)}</Badge>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Visualizar</a>
                <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300">Baixar</a>
                {canEdit && (
                  <>
                    <button type="button" onClick={() => reviewDocument(doc.id, 'aprovado')} className="rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400">Aprovar</button>
                    <button type="button" onClick={() => reviewDocument(doc.id, 'reenvio_solicitado')} className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">Solicitar ajuste</button>
                    <button type="button" onClick={() => deleteDocument(doc.id)} className="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-400">Excluir</button>
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
