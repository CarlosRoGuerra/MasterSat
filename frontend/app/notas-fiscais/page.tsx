'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  FileText, RefreshCw, CheckCircle2, AlertTriangle, Loader2, Receipt,
  ListChecks, ChevronLeft, ExternalLink, FileDown, FileCode2, Search,
  LayoutDashboard, Layers, Plus, SlidersHorizontal,
} from 'lucide-react';

import { PageShell } from '@/components/page-shell';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableHead, Th, TableBody, Tr, Td } from '@/components/ui/table';
import { EmptyState } from '@/components/ui/empty-state';
import { MetricCard } from '@/components/ui/metric-card';
import { Badge } from '@/components/ui/badge';
import { ErrorBanner } from '@/components/ui/error-banner';
import { DonutChart } from '@/components/ui/donut-chart';
import { Pagination } from '@/components/ui/pagination';
import { apiFetch, API_URL } from '@/lib/api';
import { useAuthGuard } from '@/lib/use-auth-guard';

/* ── Tipos ─────────────────────────────────────────────────────────────── */
type Elegivel = {
  billing_id: number; client_id: number; tomador: string; cpf_cnpj: string | null;
  tipo: string | null; cidade: string | null; nosso_numero: string | null;
  valor: number; titulo: string | null; reprocessamento: boolean;
};
type Elegiveis = { period_label: string; total_elegiveis: number; ja_emitidas: number; itens: Elegivel[] };

type LoteResumo = {
  id: number; period_label: string; competencia: string | null; codigo_servico: string | null;
  discriminacao: string | null; status: string; total_notas: number; total_autorizadas: number;
  total_erro: number; criado_em: string | null; concluido_em: string | null;
};
type LoteNota = {
  nota_id: number; billing_id: number; tomador: string; cpf_cnpj: string | null; valor: number;
  numero_nfse: string | null; status: string; chave_acesso: string | null;
  link_visualizacao: string | null; erro_codigo: string | null; erro_mensagem: string | null;
};
type LoteDetalhe = LoteResumo & { itens: LoteNota[] };

type Nota = {
  nota_id: number; billing_id: number; lote_id: number | null; tomador: string;
  cpf_cnpj: string | null; valor: number; nosso_numero: string | null; numero_nfse: string | null;
  status: string; chave_acesso: string | null; link_visualizacao: string | null;
  erro_codigo: string | null; erro_mensagem: string | null; tem_xml: boolean;
  data_ocorrencia: string | null;
};
type Notas = { total: number; limit: number; offset: number; itens: Nota[] };
type Resumo = {
  competencia: string; autorizadas: number; negadas: number; processando: number;
  total: number; total_geral: number;
};

type Aba = 'painel' | 'notas' | 'lotes' | 'gerar';

/* ── Códigos de tributação nacional usados pela MasterSat ──────────────── */
const CODIGOS_SERVICO = [
  { codigo: '110201', rotulo: '11.02.01 — Monitoramento / rastreamento (mensalidade)' },
  { codigo: '140101', rotulo: '14.01.01 — Manutenção / instalação em veículos' },
  { codigo: '150307', rotulo: '15.03.07 — Locação de equipamentos (aluguel do rastreador)' },
];

/* ── Helpers ───────────────────────────────────────────────────────────── */
const brl = (v: number) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const dataBR = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('pt-BR') : '—');

/** input <month> (YYYY-MM) → period_label do faturamento (MM/YYYY). */
function monthToPeriod(month: string): string {
  const [y, m] = month.split('-');
  return y && m ? `${m}/${y}` : '';
}

function statusBadge(status: string) {
  switch (status) {
    case 'emitida':
      return <Badge variant="success">Autorizada</Badge>;
    case 'concluido':
      return <Badge variant="success">Concluído</Badge>;
    case 'erro':
      return <Badge variant="danger">Negada</Badge>;
    case 'com_erro':
      return <Badge variant="danger">Com erro</Badge>;
    case 'processing':
    case 'processando':
    case 'pending':
      return <Badge variant="warning">Processando</Badge>;
    default:
      return <Badge>{status}</Badge>;
  }
}

const inputCls =
  'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900';

function Campo({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`text-sm ${className ?? ''}`}>
      <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">{label}</span>
      {children}
    </label>
  );
}

/** Botão de ação em ícone, no padrão da grid do sistema antigo. */
function AcaoIcone({
  title, onClick, href, children, tone = 'brand',
}: {
  title: string; onClick?: () => void; href?: string;
  children: React.ReactNode; tone?: 'brand' | 'slate';
}) {
  const cls =
    `inline-flex h-7 w-7 items-center justify-center rounded-lg border transition-colors ${
      tone === 'brand'
        ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100 dark:border-brand-900 dark:bg-brand-950/40 dark:text-brand-400'
        : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400'
    }`;
  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" title={title} className={cls}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} className={cls}>
      {children}
    </button>
  );
}

/* ── Página ────────────────────────────────────────────────────────────── */
export default function NotasFiscaisPage() {
  const { token, loading: authLoading } = useAuthGuard(['admin', 'financeiro'], '/login/admin');

  const [aba, setAba] = useState<Aba>('painel');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  // Painel
  const [resumo, setResumo] = useState<Resumo | null>(null);

  // Notas
  const [notas, setNotas] = useState<Notas | null>(null);
  const [buscaNotas, setBuscaNotas] = useState('');
  const [situacaoFiltro, setSituacaoFiltro] = useState('');
  const [porPagina, setPorPagina] = useState(10);
  const [paginaNotas, setPaginaNotas] = useState(1);

  // Lotes
  const [lotes, setLotes] = useState<LoteResumo[]>([]);
  const [loteAberto, setLoteAberto] = useState<LoteDetalhe | null>(null);

  // Geração de lote
  const [month, setMonth] = useState('');
  const [codigoServico, setCodigoServico] = useState(CODIGOS_SERVICO[0].codigo);
  const [discriminacao, setDiscriminacao] = useState('');
  const [filtroNome, setFiltroNome] = useState('');
  const [filtroTipo, setFiltroTipo] = useState('');
  const [mostrarFiltros, setMostrarFiltros] = useState(false);
  const [elegiveis, setElegiveis] = useState<Elegiveis | null>(null);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [buscando, setBuscando] = useState(false);
  const [emitindo, setEmitindo] = useState(false);

  /* ── Carregamentos ── */
  const carregarResumo = useCallback(() => {
    if (!token) return;
    apiFetch<Resumo>('/nfse/resumo', {}, token).then(setResumo).catch(() => {});
  }, [token]);

  const carregarLotes = useCallback(() => {
    if (!token) return;
    apiFetch<LoteResumo[]>('/nfse/lotes', {}, token).then(setLotes).catch(() => {});
  }, [token]);

  const carregarNotas = useCallback(() => {
    if (!token) return;
    const p = new URLSearchParams({
      limit: String(porPagina),
      offset: String((paginaNotas - 1) * porPagina),
    });
    if (buscaNotas.trim()) p.set('busca', buscaNotas.trim());
    if (situacaoFiltro) p.set('situacao', situacaoFiltro);
    apiFetch<Notas>(`/nfse/notas?${p}`, {}, token).then(setNotas).catch((e) => setError(parseErr(e)));
  }, [token, porPagina, paginaNotas, buscaNotas, situacaoFiltro]);

  useEffect(() => { carregarResumo(); carregarLotes(); }, [carregarResumo, carregarLotes]);
  useEffect(() => { if (aba === 'notas') carregarNotas(); }, [aba, carregarNotas]);

  // Polling enquanto houver lote processando (na tela do lote ou na lista).
  useEffect(() => {
    const processando = loteAberto?.status === 'processando'
      || lotes.some((l) => l.status === 'processando');
    if (!processando) return;
    const id = setInterval(async () => {
      try {
        if (loteAberto) {
          const d = await apiFetch<LoteDetalhe>(`/nfse/lotes/${loteAberto.id}`, {}, token);
          setLoteAberto(d);
        }
        carregarLotes();
        carregarResumo();
      } catch { /* mantém */ }
    }, 3000);
    return () => clearInterval(id);
  }, [loteAberto, lotes, token, carregarLotes, carregarResumo]);

  /* ── Ações ── */
  async function buscarBoletos() {
    const period = monthToPeriod(month);
    if (!period) { setError('Informe a data de competência.'); return; }
    setError(''); setFeedback(''); setBuscando(true); setElegiveis(null);
    try {
      const p = new URLSearchParams({ period_label: period });
      if (filtroNome.trim()) p.set('busca', filtroNome.trim());
      if (filtroTipo) p.set('tipo', filtroTipo);
      const data = await apiFetch<Elegiveis>(`/nfse/lotes/elegiveis?${p}`, {}, token);
      setElegiveis(data);
      setSelecionados(new Set(data.itens.map((i) => i.billing_id)));
    } catch (err) { setError(parseErr(err)); } finally { setBuscando(false); }
  }

  function toggle(id: number) {
    setSelecionados((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
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
      setElegiveis(null); setSelecionados(new Set());
      carregarLotes(); carregarResumo();
      abrirLote(lote.id);
    } catch (err) { setError(parseErr(err)); } finally { setEmitindo(false); }
  }

  async function abrirLote(id: number) {
    try {
      setLoteAberto(await apiFetch<LoteDetalhe>(`/nfse/lotes/${id}`, {}, token));
    } catch (err) { setError(parseErr(err)); }
  }

  async function baixarArquivo(billingId: number, tipo: 'danfse' | 'xml') {
    setError('');
    try {
      const resp = await fetch(`${API_URL.replace(/\/+$/, '')}/nfse/${billingId}/${tipo}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json())?.detail || detail; } catch { /* noop */ }
        throw new Error(detail);
      }
      const url = window.URL.createObjectURL(await resp.blob());
      if (tipo === 'danfse') {
        window.open(url, '_blank');
      } else {
        const a = document.createElement('a');
        a.href = url; a.download = `nfse-${billingId}.xml`;
        document.body.appendChild(a); a.click(); a.remove();
      }
      window.URL.revokeObjectURL(url);
    } catch (err) { setError(parseErr(err)); }
  }

  const totalSelecionado = elegiveis
    ? elegiveis.itens.filter((i) => selecionados.has(i.billing_id)).reduce((s, i) => s + i.valor, 0)
    : 0;

  if (authLoading) {
    return (
      <PageShell title="Notas Fiscais">
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" /> Carregando…
        </div>
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
          <MetricCard label="Negadas" value={loteAberto.total_erro} icon={<AlertTriangle className="h-4 w-4" />} />
        </div>

        <Card className="mt-4 p-0">
          <Table>
            <TableHead>
              <Th>Tomador</Th><Th>CPF/CNPJ</Th><Th className="text-right">Valor</Th>
              <Th>Número NF</Th><Th>Situação</Th><Th>Retorno</Th><Th>Ações</Th>
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
                      ? <span className="text-rose-600 dark:text-rose-400">
                          {n.erro_codigo ? `${n.erro_codigo}: ` : ''}{n.erro_mensagem}
                        </span>
                      : '—'}
                  </Td>
                  <Td>
                    {n.status === 'emitida' ? (
                      <div className="flex items-center gap-1.5">
                        <AcaoIcone title="DANFSE (PDF)" onClick={() => baixarArquivo(n.billing_id, 'danfse')}>
                          <FileDown className="h-3.5 w-3.5" />
                        </AcaoIcone>
                        <AcaoIcone title="XML da NFS-e" onClick={() => baixarArquivo(n.billing_id, 'xml')}>
                          <FileCode2 className="h-3.5 w-3.5" />
                        </AcaoIcone>
                        {n.link_visualizacao && (
                          <AcaoIcone title="Consulta pública" href={n.link_visualizacao} tone="slate">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </AcaoIcone>
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

  /* ── Telas principais ── */
  const abas: { id: Aba; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'painel', label: 'Painel', icon: LayoutDashboard },
    { id: 'notas', label: 'Notas', icon: Receipt },
    { id: 'lotes', label: 'Lotes', icon: Layers },
    { id: 'gerar', label: 'Gerar lote', icon: Plus },
  ];

  return (
    <PageShell
      title="Notas Fiscais"
      description="Emissão e acompanhamento de NFS-e pelo Emissor Nacional"
    >
      {/* Abas */}
      <div className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
        {abas.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => { setAba(id); setError(''); }}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
              aba === id
                ? 'border-brand-500 text-slate-900 dark:text-white'
                : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
            }`}
          >
            <Icon className="h-4 w-4" /> {label}
          </button>
        ))}
      </div>

      {error && <ErrorBanner message={error} />}
      {feedback && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
          {feedback}
        </div>
      )}

      {/* ── PAINEL ── */}
      {aba === 'painel' && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <MetricCard label="Autorizadas no mês" value={resumo?.autorizadas ?? '—'}
                        icon={<CheckCircle2 className="h-4 w-4" />} />
            <MetricCard label="Negadas no mês" value={resumo?.negadas ?? '—'}
                        icon={<AlertTriangle className="h-4 w-4" />} />
            <MetricCard label="Processando" value={resumo?.processando ?? '—'}
                        icon={<Loader2 className="h-4 w-4" />} />
            <MetricCard label="Total emitido (geral)" value={resumo?.total_geral ?? '—'}
                        icon={<Receipt className="h-4 w-4" />} />
          </div>

          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
              Balanço {resumo?.competencia ?? ''}
            </h2>
            <div className="mt-6 pb-2">
              <DonutChart
                slices={[
                  { label: 'Autorizadas', value: resumo?.autorizadas ?? 0, color: '#10b981' },
                  { label: 'Negadas', value: resumo?.negadas ?? 0, color: '#f43f5e' },
                  { label: 'Processando', value: resumo?.processando ?? 0, color: '#f59e0b' },
                ]}
              />
            </div>
          </Card>
        </>
      )}

      {/* ── NOTAS ── */}
      {aba === 'notas' && (
        <Card className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
            <div className="flex items-center gap-2 text-sm">
              <select value={porPagina}
                      onChange={(e) => { setPorPagina(Number(e.target.value)); setPaginaNotas(1); }}
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900">
                {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <span className="text-slate-500">resultados por página</span>
              <select value={situacaoFiltro}
                      onChange={(e) => { setSituacaoFiltro(e.target.value); setPaginaNotas(1); }}
                      className="ml-2 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900">
                <option value="">Todas as situações</option>
                <option value="emitida">Autorizada</option>
                <option value="erro">Negada</option>
                <option value="pending">Processando</option>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-500">Pesquisar</span>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  value={buscaNotas}
                  onChange={(e) => { setBuscaNotas(e.target.value); setPaginaNotas(1); }}
                  placeholder="Tomador, CNPJ ou nº da NF"
                  className="w-64 rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-sm dark:border-slate-700 dark:bg-slate-900"
                />
              </div>
            </div>
          </div>

          {!notas || notas.itens.length === 0 ? (
            <div className="p-6">
              <EmptyState icon={Receipt} title="Nenhuma nota encontrada"
                          description="As NFS-e emitidas aparecerão aqui." />
            </div>
          ) : (
            <>
              <Table>
                <TableHead>
                  <Th>Id Nota</Th><Th>Tomador</Th><Th className="text-right">Valor</Th>
                  <Th>Nosso Número</Th><Th>Número NF</Th><Th>Situação</Th><Th>Retorno</Th>
                  <Th>Data de ocorrência</Th><Th>Ações</Th>
                </TableHead>
                <TableBody>
                  {notas.itens.map((n) => (
                    <Tr key={n.nota_id}>
                      <Td className="font-semibold tabular-nums">{n.nota_id}</Td>
                      <Td className="font-medium">
                        {n.tomador}
                        {n.cpf_cnpj && <span className="block text-xs text-slate-400">{n.cpf_cnpj}</span>}
                      </Td>
                      <Td className="text-right tabular-nums">{brl(n.valor)}</Td>
                      <Td className="tabular-nums text-slate-500">{n.nosso_numero ?? '—'}</Td>
                      <Td className="tabular-nums">{n.numero_nfse ?? '—'}</Td>
                      <Td>{statusBadge(n.status)}</Td>
                      <Td className="max-w-[240px] text-xs text-slate-500">
                        {n.status === 'erro'
                          ? <span className="text-rose-600 dark:text-rose-400">
                              {n.erro_codigo ? `${n.erro_codigo}: ` : ''}{n.erro_mensagem}
                            </span>
                          : '—'}
                      </Td>
                      <Td className="text-slate-500">{dataBR(n.data_ocorrencia)}</Td>
                      <Td>
                        <div className="flex items-center gap-1.5">
                          {n.status === 'emitida' && (
                            <>
                              <AcaoIcone title="DANFSE (PDF)" onClick={() => baixarArquivo(n.billing_id, 'danfse')}>
                                <FileDown className="h-3.5 w-3.5" />
                              </AcaoIcone>
                              {n.tem_xml && (
                                <AcaoIcone title="XML da NFS-e" onClick={() => baixarArquivo(n.billing_id, 'xml')}>
                                  <FileCode2 className="h-3.5 w-3.5" />
                                </AcaoIcone>
                              )}
                              {n.link_visualizacao && (
                                <AcaoIcone title="Consulta pública" href={n.link_visualizacao} tone="slate">
                                  <ExternalLink className="h-3.5 w-3.5" />
                                </AcaoIcone>
                              )}
                            </>
                          )}
                          {n.lote_id && (
                            <AcaoIcone title={`Ver lote #${n.lote_id}`} tone="slate"
                                       onClick={() => abrirLote(n.lote_id as number)}>
                              <Layers className="h-3.5 w-3.5" />
                            </AcaoIcone>
                          )}
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </TableBody>
              </Table>
              <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                <p className="text-xs text-slate-500">
                  Mostrando de {notas.offset + 1} até {Math.min(notas.offset + notas.limit, notas.total)} de{' '}
                  {notas.total} registros
                </p>
                <Pagination
                  page={paginaNotas}
                  totalPages={Math.max(1, Math.ceil(notas.total / porPagina))}
                  total={notas.total}
                  start={notas.offset + 1}
                  end={Math.min(notas.offset + notas.limit, notas.total)}
                  onPage={setPaginaNotas}
                  className="border-0 pt-0"
                />
              </div>
            </>
          )}
        </Card>
      )}

      {/* ── LOTES ── */}
      {aba === 'lotes' && (
        <Card className="p-0">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3 dark:border-slate-800">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Listagem de lotes</h2>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={carregarLotes}><RefreshCw className="h-4 w-4" /> Atualizar</Button>
              <Button onClick={() => setAba('gerar')}><Plus className="h-4 w-4" /> Gerar</Button>
            </div>
          </div>
          {lotes.length === 0 ? (
            <div className="p-6">
              <EmptyState icon={Layers} title="Nenhum lote emitido"
                          description="Os lotes de NFS-e emitidos aparecerão aqui." />
            </div>
          ) : (
            <Table>
              <TableHead>
                <Th>Id Lote</Th><Th>Fechamento</Th><Th>Situação</Th>
                <Th className="text-center">Qtd. de notas</Th><Th className="text-center">Autorizadas</Th>
                <Th className="text-center">Negadas</Th><Th>Data de ocorrência</Th><Th>Ações</Th>
              </TableHead>
              <TableBody>
                {lotes.map((l) => (
                  <Tr key={l.id}>
                    <Td className="font-semibold tabular-nums">{l.id}</Td>
                    <Td>{l.period_label}</Td>
                    <Td>{statusBadge(l.status)}</Td>
                    <Td className="text-center tabular-nums">{l.total_notas}</Td>
                    <Td className="text-center tabular-nums text-emerald-600 dark:text-emerald-400">{l.total_autorizadas}</Td>
                    <Td className="text-center tabular-nums text-rose-600 dark:text-rose-400">{l.total_erro}</Td>
                    <Td className="text-slate-500">{dataBR(l.criado_em)}</Td>
                    <Td><Button variant="ghost" onClick={() => abrirLote(l.id)}>Visualizar</Button></Td>
                  </Tr>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      )}

      {/* ── GERAR LOTE ── */}
      {aba === 'gerar' && (
        <>
          {/* Observações */}
          <div className="rounded-xl border border-sky-200 bg-sky-50 px-5 py-4 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
            <p className="font-semibold">Observações</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>O campo <strong>Emitir Nota Fiscal</strong> precisa estar marcado no cadastro do cliente.</li>
              <li>O <strong>endereço do tomador</strong> deve estar completo (logradouro, bairro, CEP e UF).</li>
              <li>A <strong>tributação</strong> selecionada deve corresponder ao serviço prestado.</li>
              <li>Cobranças que já têm nota autorizada não aparecem na listagem.</li>
            </ul>
          </div>

          {/* Emitente */}
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Emitente</h2>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA
              <span className="ml-2 text-xs text-slate-400">CNPJ 14.228.344/0001-67</span>
            </p>
          </Card>

          {/* Identificação dos serviços */}
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Identificação dos serviços</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Campo label="Data de competência *">
                <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className={inputCls} />
              </Campo>
              <Campo label="Serviço do município *">
                <select value={codigoServico} onChange={(e) => setCodigoServico(e.target.value)} className={inputCls}>
                  {CODIGOS_SERVICO.map((c) => <option key={c.codigo} value={c.codigo}>{c.rotulo}</option>)}
                </select>
              </Campo>
              <Campo label="Discriminação / observação" className="sm:col-span-2">
                <textarea value={discriminacao} onChange={(e) => setDiscriminacao(e.target.value)} rows={2}
                          placeholder="Descrição do serviço no corpo da nota"
                          className={inputCls} />
              </Campo>
            </div>
          </Card>

          {/* Filtros */}
          <Card className="p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Filtros</h2>
              <Button variant="secondary" onClick={() => setMostrarFiltros((v) => !v)}>
                <SlidersHorizontal className="h-4 w-4" /> {mostrarFiltros ? 'Ocultar filtros' : 'Exibir filtros'}
              </Button>
            </div>
            {mostrarFiltros && (
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Campo label="Nome / Razão social">
                  <input value={filtroNome} onChange={(e) => setFiltroNome(e.target.value)}
                         placeholder="Filtrar por nome ou CPF/CNPJ" className={inputCls} />
                </Campo>
                <Campo label="Tipo de pessoa">
                  <select value={filtroTipo} onChange={(e) => setFiltroTipo(e.target.value)} className={inputCls}>
                    <option value="">Todos</option>
                    <option value="pf">Pessoa física</option>
                    <option value="pj">Pessoa jurídica</option>
                  </select>
                </Campo>
              </div>
            )}
            <div className="mt-4">
              <Button onClick={buscarBoletos} disabled={buscando || !month}>
                {buscando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                Buscar boletos
              </Button>
            </div>
          </Card>

          {/* Listagem de boletos */}
          {elegiveis && (
            elegiveis.itens.length === 0 ? (
              <Card className="p-6">
                <EmptyState
                  icon={ListChecks}
                  title="Nenhum registro encontrado"
                  description={
                    elegiveis.ja_emitidas > 0
                      ? `Lote já processado — ${elegiveis.ja_emitidas} nota(s) já emitida(s) neste fechamento.`
                      : 'Nenhuma cobrança com "Emitir NF = Sim" pendente neste fechamento.'
                  }
                />
              </Card>
            ) : (
              <Card className="p-0">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                    Listagem de boletos — {elegiveis.period_label}
                  </h2>
                  <div className="flex items-center gap-4 text-sm">
                    <span className="text-slate-500">
                      <strong className="text-slate-900 dark:text-white">{selecionados.size}</strong> de{' '}
                      {elegiveis.itens.length} selecionada(s)
                    </span>
                    <span className="font-semibold tabular-nums text-slate-900 dark:text-white">
                      {brl(totalSelecionado)}
                    </span>
                    <Button onClick={emitir} disabled={emitindo || selecionados.size === 0}>
                      {emitindo ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                      Emitir
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
                    <Th>Nosso número</Th><Th>Nome</Th><Th>CPF/CNPJ</Th><Th>Cidade</Th>
                    <Th className="text-right">Valor</Th>
                  </TableHead>
                  <TableBody>
                    {elegiveis.itens.map((i) => (
                      <Tr key={i.billing_id} selected={selecionados.has(i.billing_id)}>
                        <Td>
                          <input type="checkbox" aria-label={`Selecionar ${i.tomador}`}
                                 checked={selecionados.has(i.billing_id)} onChange={() => toggle(i.billing_id)} />
                        </Td>
                        <Td className="tabular-nums text-slate-500">{i.nosso_numero ?? '—'}</Td>
                        <Td className="font-medium">
                          {i.tomador}
                          {i.reprocessamento && <Badge variant="warning" className="ml-2">Reprocessar</Badge>}
                        </Td>
                        <Td className="text-slate-500">{i.cpf_cnpj ?? '—'}</Td>
                        <Td className="text-slate-500">{i.cidade ?? '—'}</Td>
                        <Td className="text-right tabular-nums">{brl(i.valor)}</Td>
                      </Tr>
                    ))}
                  </TableBody>
                </Table>
              </Card>
            )
          )}
        </>
      )}
    </PageShell>
  );
}

function parseErr(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) return String((err as { message: unknown }).message);
  return 'Erro ao processar a solicitação.';
}
