/**
 * Utilitário para downloads de exportação via API autenticada.
 * Suporta CSV e Excel (xlsx).
 */

import { API_URL } from '@/lib/api';

export type ExportFormat = 'csv' | 'xlsx';

export async function downloadExport(
  path: string,
  params: Record<string, string>,
  token: string,
  filename: string,
): Promise<void> {
  const url = new URL(`${API_URL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`);
  Object.entries(params).forEach(([k, v]) => { if (v) url.searchParams.set(k, v); });

  const response = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    throw new Error(`Erro ao exportar: ${response.statusText}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

export function exportFilename(base: string, fmt: ExportFormat): string {
  const date = new Date().toISOString().slice(0, 10);
  return `${base}_${date}.${fmt}`;
}
