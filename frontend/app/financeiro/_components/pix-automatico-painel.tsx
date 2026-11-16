'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, QrCode, RefreshCw } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { formatCurrency, formatDate, formatPhone } from '@/lib/format';
import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ClientPixAutomatico } from '../../clientes/_components/client-pix-automatico';

export type PixPainelLinha = {
  client_id: number; cliente: string | null; telefone?: string | null; billing_id: number | null; cobranca_pix_id: number | null;
  vencimento: string | null; valor: string | number | null; situacao: string; situacao_label: string;
  acao: string; acao_label: string; gravidade: 'alta' | 'media' | 'info'; detalhe: string | null;
  convite_status: string | null;
};
export type PixPainel = {
  ambiente: string; hoje: string; habilitado: boolean; escrita_habilitada: boolean;
  resumo: { autorizacoes_pendentes: number; debitos_nao_realizados: number; total_aguardando_confirmacao: string | number;
    clientes_a_contatar: number; falhas_integracao: number; acoes_necessarias: number };
  linhas: PixPainelLinha[];
};

const GRAVIDADE = { alta: 'danger', media: 'warning', info: 'default' } as const;

function Indicador({ rotulo, valor, alerta }: { rotulo: string; valor: string | number; alerta?: boolean }) {
  return (
    <div className={['rounded-xl border p-3', alerta
      ? 'border-rose-200 bg-rose-50 dark:border-rose-900/40 dark:bg-rose-950/20'
      : 'border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900'].join(' ')}>
      <p className="text-xs text-slate-500 dark:text-slate-400">{rotulo}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{valor}</p>
    </div>
  );
}

/** Botão com contador + modal de acompanhamento do Pix Automático (somente leitura). */
export function PixAutomaticoPainel({ token }: { token: string | null }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<PixPainel | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [incluirResolvidos, setIncluirResolvidos] = useState(false);
  const [cliente, setCliente] = useState<{ id: number; name: string; phone?: string | null } | null>(null);

  const load = useCallback(async (incluir: boolean) => {
    if (!token) return;
    setLoading(true); setError('');
    try {
      const body = await apiFetch<PixPainel>(`/sicredi-pix/painel${incluir ? '?incluir_resolvidos=true' : ''}`, {}, token);
      // Resposta fora do formato (ex.: backend anterior a este painel) não derruba o Financeiro.
      if (!body || typeof body !== 'object' || !body.resumo || !Array.isArray(body.linhas)) {
        setData(null);
        setError('Painel Pix Automático indisponível neste servidor.');
        return;
      }
      setData(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível carregar o painel Pix Automático.');
    } finally { setLoading(false); }
  }, [token]);

  useEffect(() => { void load(false); }, [load]);

  const acoes = data?.resumo.acoes_necessarias ?? 0;
  const resumo = data?.resumo;

  return (
    <>
      <Button type="button" variant="secondary" aria-haspopup="dialog" onClick={() => { setOpen(true); void load(incluirResolvidos); }}>
        <QrCode aria-hidden="true" className="h-4 w-4" />
        Pix Automático
        <span className={['ml-1 rounded-md px-2 py-0.5 text-xs font-semibold tabular-nums', acoes
          ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-200'
          : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'].join(' ')}
          aria-label={`${acoes} ação(ões) necessária(s)`}>
          {data ? acoes : '—'}
        </span>
      </Button>
      <Modal open={open} onClose={() => { setOpen(false); setCliente(null); void load(false); }}
        title="Pix Automático — acompanhamento" size="2xl"
        description="Autorizações, agendamentos e débitos que precisam da equipe. Situações calculadas a partir dos registros do Sicredi; nada é alterado aqui.">
        {cliente && token ? (
          <div className="space-y-3">
            <Button type="button" variant="ghost" onClick={() => { setCliente(null); void load(incluirResolvidos); }}>
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />Voltar ao painel
            </Button>
            <p className="text-sm font-semibold">{cliente.name}</p>
            <ClientPixAutomatico key={cliente.id} client={{ id: cliente.id, name: cliente.name, phone: cliente.phone }} token={token}
              onUpdated={() => void load(incluirResolvidos)} />
          </div>
        ) : (
          <div className="space-y-4">
            {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}
            {data && !data.escrita_habilitada && (
              <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
                Autorizações e agendamentos Pix não estão liberados neste ambiente ({data.ambiente}). O painel mostra a situação registrada.
              </p>
            )}
            {resumo && (
              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
                <Indicador rotulo="Autorizações pendentes" valor={resumo.autorizacoes_pendentes} />
                <Indicador rotulo="Débitos não realizados" valor={resumo.debitos_nao_realizados} alerta={resumo.debitos_nao_realizados > 0} />
                <Indicador rotulo="Aguardando confirmação" valor={formatCurrency(Number(resumo.total_aguardando_confirmacao))} />
                <Indicador rotulo="Clientes a contatar" valor={resumo.clientes_a_contatar} alerta={resumo.clientes_a_contatar > 0} />
                <Indicador rotulo="Falhas de integração" valor={resumo.falhas_integracao} alerta={resumo.falhas_integracao > 0} />
              </div>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={incluirResolvidos}
                  onChange={(e) => { setIncluirResolvidos(e.target.checked); void load(e.target.checked); }} />
                Mostrar também agendados e pagos
              </label>
              <Button type="button" variant="ghost" disabled={loading} onClick={() => void load(incluirResolvidos)}>
                <RefreshCw className="h-4 w-4" aria-hidden="true" />Atualizar
              </Button>
            </div>
            {loading && !data && <p className="text-sm text-slate-500">Carregando…</p>}
            {data && !data.linhas.length && <p className="text-sm text-slate-500">Nenhuma situação Pix exige ação agora.</p>}
            {data && Boolean(data.linhas.length) && (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="py-2 pr-3">Cliente</th><th className="py-2 pr-3">Vencimento</th><th className="py-2 pr-3">Valor</th>
                      <th className="py-2 pr-3">Situação Pix</th><th className="py-2 pr-3">Próxima ação</th><th className="py-2"><span className="sr-only">Abrir</span></th></tr>
                  </thead>
                  <tbody>
                    {data.linhas.map((linha) => (
                      <tr key={`${linha.client_id}-${linha.billing_id ?? 'aut'}`} className="border-t border-slate-100 align-top dark:border-slate-800">
                        <td className="py-2 pr-3">{linha.cliente}
                          {linha.telefone && <span className="block text-xs text-slate-500">{formatPhone(linha.telefone)}</span>}
                          {linha.billing_id && <span className="block text-xs text-slate-500">Cobrança #{linha.billing_id}</span>}</td>
                        <td className="py-2 pr-3 tabular-nums">{linha.vencimento ? formatDate(linha.vencimento) : '—'}</td>
                        <td className="py-2 pr-3 tabular-nums">{linha.valor != null ? formatCurrency(Number(linha.valor)) : '—'}</td>
                        <td className="py-2 pr-3">
                          <Badge variant={GRAVIDADE[linha.gravidade]}>{linha.situacao_label}</Badge>
                          {linha.detalhe && <span className="mt-1 block text-xs text-slate-500">{linha.detalhe}</span>}
                        </td>
                        <td className="py-2 pr-3">{linha.acao_label}</td>
                        <td className="py-2 text-right">
                          <Button type="button" variant="ghost" onClick={() => setCliente({ id: linha.client_id,
                            name: linha.cliente ?? `Cliente #${linha.client_id}`, phone: linha.telefone })}>
                            Abrir
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
