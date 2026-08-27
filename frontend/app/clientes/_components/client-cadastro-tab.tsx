import { Badge, statusLabel, statusVariant } from '@/components/ui/badge';
import { formatCpfCnpj, formatPhone } from '@/lib/format';
import type { Client, VehicleSummary } from './types';

export function ClientCadastroTab({ client, vehicles }: { client: Client; vehicles: VehicleSummary[] }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Documento</p>
          <p className="mt-2 font-semibold text-slate-900 dark:text-white">{formatCpfCnpj(client.cpf_cnpj)}</p>
        </div>
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Status</p>
          <div className="mt-2"><Badge variant={statusVariant(client.status)}>{statusLabel(client.status)}</Badge></div>
        </div>
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">E-mail</p>
          <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{client.email || 'Não informado'}</p>
        </div>
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Telefone</p>
          <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{client.phone ? formatPhone(client.phone) : 'Não informado'}</p>
        </div>
        <div className="rounded-xl border border-brand-100 bg-brand-50/60 p-4 dark:border-brand-900/40 dark:bg-brand-950/30">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-brand-500 dark:text-brand-400">Dia de vencimento</p>
          <p className="mt-2 text-sm font-semibold text-brand-800 dark:text-brand-200">
            {client.billing_day ? `Todo dia ${client.billing_day}` : 'Não configurado'}
          </p>
          <p className="mt-0.5 text-xs text-brand-500/70 dark:text-brand-400/60">Padrão para novos contratos</p>
        </div>
      </div>
      <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">Endereço</p>
        <p className="mt-2 text-sm leading-6 text-slate-700 dark:text-slate-300">
          {[client.address_line, client.address_number, client.neighborhood, client.city, client.state].filter(Boolean).join(', ') || 'Não informado'}
        </p>
      </div>
      {(client.contacts || []).length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Contatos adicionais</p>
          <div className="space-y-2">
            {(client.contacts || []).map((c, i) => (
              <div key={i} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/50">
                <p className="font-medium text-slate-900 dark:text-white">{c.name}{c.role && <span className="ml-2 text-xs font-normal text-slate-400">({c.role})</span>}</p>
                <p className="mt-0.5 text-xs text-slate-500">{[c.phone && formatPhone(c.phone), c.email].filter(Boolean).join(' · ')}</p>
              </div>
            ))}
          </div>
        </div>
      )}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Veículos vinculados</p>
        {vehicles.length === 0 ? (
          <p className="text-sm text-slate-400">Nenhum veículo vinculado.</p>
        ) : (
          <div className="space-y-1.5">
            {vehicles.map((v) => (
              <div key={v.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900/50">
                <span className="font-medium text-slate-900 dark:text-white">{v.plate}</span>
                <Badge variant={statusVariant(v.status)}>{statusLabel(v.status)}</Badge>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
