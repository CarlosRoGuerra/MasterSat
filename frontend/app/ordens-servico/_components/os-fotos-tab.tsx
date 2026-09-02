'use client';

import { useState } from 'react';
import { ImagePlus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { apiFetch } from '@/lib/api';
import { OrderDocument, fieldClass, parseError } from './types';

const PHOTO_CATEGORIES = ['evidencia_fotografica', 'anexo_tecnico'];

export function OsFotosTab({
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
  const [category, setCategory] = useState(PHOTO_CATEGORIES[0]);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);

  const photos = documents.filter((doc) => (doc.content_type ?? '').startsWith('image/'));

  async function upload() {
    if (!files.length) return;
    setUploading(true);
    onError('');
    try {
      const body = new FormData();
      body.append('category', category);
      files.forEach((file) => body.append('files', file));
      await apiFetch(`/service-orders/${orderId}/documents`, { method: 'POST', body }, token);
      onFeedback('Fotos enviadas com sucesso.');
      setFiles([]);
      await onRefresh();
    } catch (err) {
      onError(parseError(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-4">
      {canEdit && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
          <select className={fieldClass} style={{ width: 200 }} value={category} onChange={(e) => setCategory(e.target.value)}>
            {PHOTO_CATEGORIES.map((c) => <option key={c} value={c}>{c === 'evidencia_fotografica' ? 'Evidência fotográfica' : 'Anexo técnico'}</option>)}
          </select>
          <input
            type="file"
            multiple
            accept="image/*"
            className={`${fieldClass} file:mr-3 file:rounded-lg file:border-0 file:bg-brand-700 file:px-3 file:py-1.5 file:text-xs file:text-white`}
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
          />
          <Button disabled={!files.length || uploading} onClick={upload} className="gap-1.5">
            <ImagePlus className="h-4 w-4" /> {uploading ? 'Enviando…' : 'Enviar fotos'}
          </Button>
        </div>
      )}

      {photos.length === 0 ? (
        <EmptyState title="Sem fotos" description="Nenhuma foto anexada a esta OS ainda." />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {photos.map((photo) => (
            <a
              key={photo.id}
              href={photo.url}
              target="_blank"
              rel="noreferrer"
              className="group overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/50"
            >
              <img src={photo.url} alt={photo.file_name} className="h-32 w-full object-cover transition group-hover:opacity-80" />
              <p className="truncate px-2 py-1.5 text-xs text-slate-500">{photo.file_name}</p>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
