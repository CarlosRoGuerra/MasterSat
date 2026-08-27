import { FileText } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { contractSituacao, envioMeta, fileInputClass } from './helpers';
import type { Client, ClientDocument, ContractSheetItem } from './types';

export type ContractCheck = { level: string; message: string };

export function ContractSheetModal({
  open,
  client,
  loading,
  items,
  docs,
  signAlvo,
  file,
  check,
  uploading,
  canEdit,
  onClose,
  onSignAlvoChange,
  onFileChange,
  onUpload,
  onView,
  onDeleteContract,
  onDeleteDoc,
}: {
  open: boolean;
  client: Client | null;
  loading: boolean;
  items: ContractSheetItem[];
  docs: ClientDocument[];
  signAlvo: string;
  file: File | null;
  check: ContractCheck | null;
  uploading: boolean;
  canEdit: boolean;
  onClose: () => void;
  onSignAlvoChange: (value: string) => void;
  onFileChange: (file: File | null) => void;
  onUpload: () => void;
  onView: (contractId: number) => void;
  onDeleteContract: (contractId: number) => void;
  onDeleteDoc: (docId: number) => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={client ? `Contratos — ${client.name}` : 'Contratos'}
      size="2xl"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Gere o modelo em branco em <strong>Financeiro → Gerar contrato</strong>. Quando o cliente devolver assinado, anexe aqui.
        </p>
        {client && (
          client.contrato_armazenado
            ? <Badge variant="success">Contrato armazenado</Badge>
            : <Badge variant="warning">Assinado pendente</Badge>
        )}
      </div>

      {/* Contratos vinculados (registros): visualizar e cancelar/excluir. */}
      <div className="mb-4">
        <p className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">Contratos vinculados ({items.length})</p>
        {loading ? (
          <p className="text-xs text-slate-400">Carregando…</p>
        ) : items.length === 0 ? (
          <p className="text-xs text-slate-400">Nenhum contrato vinculado a este cliente.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wider text-slate-500 dark:border-slate-700 dark:bg-slate-800/50">
                  <th className="px-3 py-2 font-semibold">Plano</th>
                  <th className="px-3 py-2 font-semibold">Vínculo</th>
                  <th className="px-3 py-2 font-semibold">Vigência</th>
                  <th className="px-3 py-2 font-semibold">Situação</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.map((c) => {
                  const sit = contractSituacao(c);
                  return (
                    <tr key={c.id} className="hover:bg-slate-50/70 dark:hover:bg-slate-800/40">
                      <td className="px-3 py-2"><span className="text-xs text-slate-400">#{c.id}</span> {c.plan_name ?? '—'}</td>
                      <td className="px-3 py-2 text-xs text-slate-500">{c.vehicle_plate || c.tracker_identifier || 'Geral'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs">
                        {c.start_date ? new Date(c.start_date + 'T12:00:00').toLocaleDateString('pt-BR') : '—'}
                        {' → '}
                        {c.end_date ? new Date(c.end_date + 'T12:00:00').toLocaleDateString('pt-BR') : 'Indeterminada'}
                      </td>
                      <td className="px-3 py-2"><Badge variant={sit.variant}>{sit.label}</Badge></td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1.5">
                          <button type="button" onClick={() => onView(c.id)} className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-100 dark:border-brand-900/40 dark:bg-brand-950/30 dark:text-brand-400">Ver</button>
                          {canEdit && (
                            <button type="button" onClick={() => onDeleteContract(c.id)} className="rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-900/40 dark:text-rose-400 dark:hover:bg-rose-950/30">Excluir</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Contrato assinado: sobe aqui mesmo, já na categoria certa. */}
      <div className="mb-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Contrato assinado</p>
          <span className="text-xs text-slate-400">{docs.length} arquivo(s)</span>
        </div>
        <p className="mt-0.5 text-xs text-slate-400">Depois que o cliente devolver o contrato assinado, anexe o arquivo (PDF ou imagem) aqui. O sistema confere se ele foi preenchido antes de guardar.</p>

        {canEdit && items.some((c) => c.status !== 'cancelado' && c.status !== 'encerrado') && (
          <div className="mt-3">
            <p className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">Ao enviar, colocar &quot;em vigor&quot; o contrato:</p>
            <Select value={signAlvo} onChange={(e) => onSignAlvoChange(e.target.value)} className="w-full">
              <option value="">Não vincular — só guardar o arquivo</option>
              {items.filter((c) => c.status !== 'cancelado' && c.status !== 'encerrado').map((c) => (
                <option key={c.id} value={c.id}>#{c.id} • {c.plan_name || 'Plano'}{c.vehicle_plate ? ` • ${c.vehicle_plate}` : ''}{c.signed ? ' — já assinado' : ''}</option>
              ))}
            </Select>
          </div>
        )}

        {canEdit && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              type="file"
              accept="application/pdf,image/*"
              className={fileInputClass}
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            />
            <Button type="button" disabled={uploading || !file} onClick={onUpload}>
              {uploading ? 'Conferindo…' : 'Enviar contrato assinado'}
            </Button>
          </div>
        )}

        {check && (
          <p className={[
            'mt-3 rounded-xl border px-3 py-2 text-xs',
            check.level === 'ok'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-400'
              : 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400',
          ].join(' ')}>
            {check.message}
          </p>
        )}

        {loading ? (
          <p className="mt-3 text-xs text-slate-400">Carregando…</p>
        ) : docs.length === 0 ? (
          <p className="mt-3 text-xs text-slate-400">Nenhum contrato assinado enviado ainda.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {docs.map((doc) => (
              <li key={doc.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
                <span className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                  <span className="flex flex-col">
                    <span>{doc.file_name}</span>
                    {envioMeta(doc) && <span className="text-[11px] text-slate-400">{envioMeta(doc)}</span>}
                  </span>
                </span>
                <span className="flex items-center gap-1.5">
                  <a href={doc.url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Visualizar</a>
                  <a href={doc.download_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Baixar</a>
                  {canEdit && (
                    <button type="button" onClick={() => onDeleteDoc(doc.id)} className="rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-900/40 dark:text-rose-400 dark:hover:bg-rose-950/30">Excluir</button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
