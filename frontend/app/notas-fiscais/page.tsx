'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  FileText, RefreshCw, CheckCircle2, AlertTriangle, Loader2,
  Receipt, ListChecks, ChevronLeft, ExternalLink, FileDown,
} from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState } from '@/components/ui/empty-state';
import { MetricCard } from '@/components/ui/metric-card';
import { Badge } from '@/components/ui/badge';
import { ErrorBanner } from '@/components/ui/error-banner';
import { apiFetch, API_URL } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

/* ── Tipos ─────────────────────────────────────────────────────────────── */
type Elegivel = {
  billing_id: number;
  client_id: number;
  tomador: string;
  cpf_cnpj: string | null;
  tipo: string | null;
  valor: number;
  titulo: string | null;
  reprocessamento: boolean;
};
type Elegiveis = { period_label: string; total_elegiveis: number; ja_emitidas: number; itens: Elegivel[] };

type LoteResumo = {
  id: number;
  period_label: string;
  competencia: string | null;
  codigo_servico: string | null;
  discriminacao: string | null;
  status: string;
  total_notas: number;
  total_autorizadas: number;
  total_erro: number;
  criado_em: string | null;
  concluido_em: string | null;
};
type LoteNota = {
  nota_id: number;
  billing_id: number;
  tomador: string;
  cpf_cnpj: string | null;
  valor: number;
  numero_nfse: string | null;
  status: string;
  chave_acesso: string | null;
  link_visualizacao: string | null;
  erro_codigo: string | null;
  erro_mensagem: string | null;
};
type LoteDetalhe = LoteResumo & { itens: LoteNota[] };

/* ── Helpers ───────────────────────────────────────────────────────────── */
const brl = (v: number) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

/** input <month> (YYYY-MM) → period_label do faturamento (MM/YYYY). */
function monthToPeriod(month: string): string {
  const [y, m] = month.split('-');
  return y && m ? `${m}/${y}` : '';
}

function statusBadge(status: string) {
  switch (status) {
    case 'emitida':
    case 'concluido':
      return <Badge variant="success">{status === 'emitida' ? 'Autorizada' : 'Concluído'}</Badge>;
    case 'processing':
    case 'processando':
    case 'pending':
      return <Badge variant="warning">Processando</Badge>;
    case 'erro':
    case 'com_erro':
      return <Badge variant="danger">Com erro</Badge>;
    default:
      return <Badge>{status}</Badge>;
  }
}

/* ── Página ────────────────────────────────────────────────────────────── */
export default function NotasFiscaisPage() {
  const { token, loading: authLoading } = useAuthGuard(['admin', 'financeiro'], '/login/admin');

  // Etapa 1 — parâmetros
  const [month, setMonth] = useState('');
  const [codigoServico, setCodigoServico] = useState('11.02');
  const [discriminacao, setDiscriminacao] = useState('');

  // Etapa 2 — conferência
  const [elegiveis, setElegiveis] = useState<Elegiveis | null>(null);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [buscando, setBuscando] = useState(false);

  // Emissão + monitoramento
  const [emitindo, setEmitindo] = useState(false);
  const [lotes, setLotes] = useState<LoteResumo[]>([]);
  const [loteAberto, setLoteAberto] = useState<LoteDetalhe | null>(null);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  const carregarLotes = useCallback(() => {
    if (!token) return;
    apiFetch<LoteResumo[]>('/nfse/lotes', {}, token).then(setLotes).catch(() => {});
  }, [token]);

  useEffect(() => { carregarLotes(); }, [carregarLotes]);

  // Enquanto houver lote processando, faz polling do detalhe aberto e da lista.
  useEffect(() => {
    if (!loteAberto || loteAberto.status !== 'processando') return;
    const id = setInterval(async () => {
      try {
        const d = await apiFetch<LoteDetalhe>(`/nfse/lotes/${loteAberto.id}`, {}, token);
        setLoteAberto(d);
        if (d.status !== 'processando') carregarLotes();
      } catch { /* mantém */ }
    }, 3000);
    return () => clearInterval(id);
  }, [loteAberto, token, carregarLotes]);

  async function buscarLote() {
    const period = monthToPeriod(month);
    if (!period) { setError('Selecione o mês do lote de fechamento.'); return; }
    setError(''); setFeedback(''); setBuscando(true); setElegiveis(null);
    try {
      const data = await apiFetch<Elegiveis>(
        `/nfse/lotes/elegiveis?period_label=${encodeURIComponent(period)}`, {}, token,
      );
      setElegiveis(data);
      setSelecionados(new Set(data.itens.map((i) => i.billing_id)));  // todos marcados
    } catch (err) { setError(parseErr(err)); } finally { setBuscando(false); }
  }

  function toggle(id: number) {
    setSelecionados((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }
  function toggleTodos() {
    if (!elegiveis) return;
    setSelecionados((prev) =>
      prev.size === elegiveis.itens.length ? new Set() : new Set(elegiveis.itens.map((i) => i.billing_id)),
    );
  }

  async function emitir() {
    if (!elegiveis || selecionados.size === 0) return;
    setError(''); setFeedback(''); setEmitindo(true);
    try {
      const lote = await apiFetch<LoteResumo>('/nfse/lotes', {
        method: 'POST',
        body: JSON.stringify({
          period_label: elegiveis.period_label,
          billing_ids: [...selecionados],
          competencia: month ? `${month}-01` : null,
          codigo_servico: codigoServico || null,
          discriminacao: discriminacao || null,
        }),
      }, token);
      setFeedback(`Lote #${lote.id} enviado para emissão (${lote.total_notas} nota(s)).`);
      setElegiveis(null);
      setSelecionados(new Set());
      carregarLotes();
      abrirLote(lote.id);
    } catch (err) { setError(parseErr(err)); } finally { setEmitindo(false); }
  }

  async function abrirLote(id: number) {
    try {
      setLoteAberto(await apiFetch<LoteDetalhe>(`/nfse/lotes/${id}`, {}, token));
    } catch (err) { setError(parseErr(err)); }
  }

  async function baixarDanfse(billingId: number) {
    setError('');
    try {
      const url = `${API_URL.replace(/\/+$/, '')}/nfse/${billingId}/danfse`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json())?.detail || detail; } catch { /* noop */ }
        throw new Error(detail);
      }
      const blob = await resp.blob();
      window.open(window.URL.createObjectURL(blob), '_blank');
    } catch (err) { setError(parseErr(err)); }
  }

  const totalSelecionado = elegiveis
    ? elegiveis.itens.filter((i) => selecionados.has(i.billing_id)).reduce((s, i) => s + i.valor, 0)
    : 0;

  if (authLoading) {
    return (
      <PageShell title="Notas Fiscais">
        <div className="flex items-center gap-2 text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Carregando…</div>
      </PageShell>
    );
  }

  /* ── Drill-down de um lote ── */
  if (loteAberto) {
    return (
      <PageShell
        title={`Lote de NFS-e #${loteAberto.id}`}
        description={`Fechamento ${loteAberto.period_label}`}
        actions={
          <Button variant="secondary" onClick={() => { setLoteAberto(null); carregarLotes(); }}>
            <ChevronLeft className="h-4 w-4" /> Voltar
          </Button>
        }
      >
        {error && <ErrorBanner message={error} />}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Situação" value={statusBadge(loteAberto.status)} />
          <MetricCard label="Notas" value={loteAberto.total_notas} icon={<Receipt className="h-4 w-4" />} />
          <MetricCard label="Autorizadas" value={loteAberto.total_autorizadas} icon={<CheckCircle2 className="h-4 w-4" />} />
          <MetricCard label="Com erro" value={loteAberto.total_erro} icon={<AlertTriangle className="h-4 w-4" />} />
        </div>

        <Card className="mt-4 p-0">
          <Table>
            <TableHead>
              <Th>Tomador</Th><Th>CPF/CNPJ</Th><Th className="text-right">Valor</Th>
              <Th>Nº NFS-e</Th><Th>Situação</Th><Th>Retorno</Th><Th>Ações</Th>
            </TableHead>
            <TableBody>
              {loteAberto.itens.map((n) => (
                <Tr key={n.nota_id}>
                  <Td className="font-medium">{n.tomador}</Td>
                  <Td className="text-slate-500">{n.cpf_cnpj ?? '—'}</Td>
                  <Td className="text-right tabular-nums">{brl(n.valor)}</Td>
                  <Td>{n.numero_nfse ?? '—'}</Td>
                  <Td>{statusBadge(n.status)}</Td>
                  <Td className="max-w-[280px] text-xs text-slate-500">
                    {n.status === 'erro'
                      ? <span className="text-rose-600 dark:text-rose-400">{n.erro_codigo ? `${n.erro_codigo}: ` : ''}{n.erro_mensagem}</span>
                      : '—'}
                  </Td>
                  <Td>
                    {n.status === 'emitida' ? (
                      <div className="flex items-center gap-3">
                        <button onClick={() => baixarDanfse(n.billing_id)}
                          className="inline-flex items-center gap-1 text-sm font-semibold text-brand-700 hover:underline dark:text-brand-400">
                          <FileDown className="h-3.5 w-3.5" /> DANFSE
                        </button>
                        {n.link_visualizacao && (
                          <a href={n.link_visualizacao} target="_blank" rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-sm text-slate-500 hover:underline">
                            Portal <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </div>
                    ) : '—'}
                  </Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        </Card>
      </PageShell>
    );
  }

  /* ── Tela principal: parâmetros + conferência + histórico ── */
  return (
    <PageShell
      title="Notas Fiscais"
      description="Emissão de NFS-e em lote a partir de um fechamento financeiro"
    >
      {error && <ErrorBanner message={error} />}
      {feedback && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
          {feedback}
        </div>
      )}

      {/* Etapa 1 — parâmetros */}
      <Card className="p-5">
        <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">1. Lote de fechamento e parâmetros fiscais</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Lote de fechamento (mês)</span>
            <input type="month" value={month} onChange={(e) => setMonth(e.target.value)}
                   className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" />
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Serviço do município</span>
            <input value={codigoServico} onChange={(e) => setCodigoServico(e.target.value)} placeholder="11.02"
                   className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" />
          </label>
          <label className="text-sm lg:col-span-2">
            <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Discriminação / observação</span>
            <input value={discriminacao} onChange={(e) => setDiscriminacao(e.target.value)}
                   placeholder="Descrição do serviço no corpo da nota"
                   className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900" />
          </label>
        </div>
        <div className="mt-4">
          <Button onClick={buscarLote} disabled={buscando || !month}>
            {buscando ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Buscar lote
          </Button>
        </div>
      </Card>

      {/* Etapa 2 — conferência */}
      {elegiveis && (
        elegiveis.itens.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={ListChecks}
              title="Nenhum registro encontrado"
              description={
                elegiveis.ja_emitidas > 0
                  ? `Lote já processado — ${elegiveis.ja_emitidas} nota(s) já emitida(s) neste fechamento.`
                  : 'Nenhuma cobrança com "Emitir NF = Sim" pendente neste lote de fechamento.'
              }
            />
          </Card>
        ) : (
          <Card className="p-0">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                2. Conferência — {elegiveis.period_label}
              </h2>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-slate-500">
                  <strong className="text-slate-900 dark:text-white">{selecionados.size}</strong> de {elegiveis.itens.length} selecionada(s)
                </span>
                <span className="tabular-nums font-semibold text-slate-900 dark:text-white">{brl(totalSelecionado)}</span>
                <Button onClick={emitir} disabled={emitindo || selecionados.size === 0}>
                  {emitindo ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                  Emitir Notas Fiscais
                </Button>
              </div>
            </div>
            <Table>
              <TableHead>
                <Th className="w-10">
                  <input type="checkbox" aria-label="Selecionar todos"
                         checked={selecionados.size === elegiveis.itens.length && elegiveis.itens.length > 0}
                         onChange={toggleTodos} />
                </Th>
                <Th>Tomador</Th><Th>CPF/CNPJ</Th><Th>Cobrança</Th><Th className="text-right">Valor</Th>
              </TableHead>
              <TableBody>
                {elegiveis.itens.map((i) => (
                  <Tr key={i.billing_id}>
                    <Td>
                      <input type="checkbox" aria-label={`Selecionar ${i.tomador}`}
                             checked={selecionados.has(i.billing_id)} onChange={() => toggle(i.billing_id)} />
                    </Td>
                    <Td className="font-medium">
                      {i.tomador}
                      {i.reprocessamento && <Badge variant="warning" className="ml-2">Reprocessar</Badge>}
                    </Td>
                    <Td className="text-slate-500">{i.cpf_cnpj ?? '—'}</Td>
                    <Td className="text-slate-500">{i.titulo ?? '—'}</Td>
                    <Td className="text-right tabular-nums">{brl(i.valor)}</Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
          </Card>
        )
      )}

      {/* Histórico de lotes */}
      <Card className="p-0">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3 dark:border-slate-800">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Lotes emitidos</h2>
          <Button variant="ghost" onClick={carregarLotes}><RefreshCw className="h-4 w-4" /> Atualizar</Button>
        </div>
        {lotes.length === 0 ? (
          <div className="p-6">
            <EmptyState icon={Receipt} title="Nenhum lote emitido" description="Os lotes de NFS-e emitidos aparecerão aqui." />
          </div>
        ) : (
          <Table>
            <TableHead>
              <Th>Lote</Th><Th>Fechamento</Th><Th>Situação</Th>
              <Th className="text-center">Notas</Th><Th className="text-center">Autorizadas</Th>
              <Th className="text-center">Erros</Th><Th>Data</Th><Th></Th>
            </TableHead>
            <TableBody>
              {lotes.map((l) => (
                <Tr key={l.id}>
                  <Td className="font-semibold">#{l.id}</Td>
                  <Td>{l.period_label}</Td>
                  <Td>{statusBadge(l.status)}</Td>
                  <Td className="text-center tabular-nums">{l.total_notas}</Td>
                  <Td className="text-center tabular-nums text-emerald-600 dark:text-emerald-400">{l.total_autorizadas}</Td>
                  <Td className="text-center tabular-nums text-rose-600 dark:text-rose-400">{l.total_erro}</Td>
                  <Td className="text-slate-500">{l.criado_em ? new Date(l.criado_em).toLocaleString('pt-BR') : '—'}</Td>
                  <Td>
                    <Button variant="ghost" onClick={() => abrirLote(l.id)}>Visualizar</Button>
                  </Td>
                </Tr>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </PageShell>
  );
}

function parseErr(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) return String((err as { message: unknown }).message);
  return 'Erro ao processar a solicitação.';
}
