// Situação do título no banco (Fase 03) — espelha app/services/titulo_bancario.py.

export type TituloBancario = {
  estado: 'sem_titulo' | 'em_registro' | 'desfecho_desconhecido' | 'registrado' | 'baixado' | 'remessa_cnab';
  canal?: string | null;
  nosso_numero?: string | null;
  baixa_status?: 'pendente' | 'solicitada' | 'confirmada' | null;
  pendencia?: string | null;
};

type Tom = 'default' | 'success' | 'warning' | 'danger' | 'info';

const ESTADOS: Record<TituloBancario['estado'], { rotulo: string; tom: Tom }> = {
  sem_titulo: { rotulo: 'Sem boleto no banco', tom: 'default' },
  em_registro: { rotulo: 'Registro em andamento', tom: 'info' },
  desfecho_desconhecido: { rotulo: 'Desfecho desconhecido — consultar', tom: 'danger' },
  registrado: { rotulo: 'Boleto registrado', tom: 'success' },
  baixado: { rotulo: 'Boleto baixado no banco', tom: 'default' },
  remessa_cnab: { rotulo: 'Em remessa CNAB', tom: 'warning' },
};

const BANCO_DO_CANAL: Record<string, string> = { ailos_api: 'Ailos', sicredi_api: 'Sicredi', sicredi_pix: 'Pix Automático Sicredi', cnab: 'CNAB' };

export const ROTULO_PENDENCIA: Record<string, string> = {
  pagamento_divergente: 'Banco informou pagamento com valor diferente do título',
  pago_em_cobranca_cancelada: 'Título pago no banco com a cobrança cancelada',
  pago_em_cobranca_removida: 'Título pago no banco com a cobrança removida',
  possivel_pagamento_duplicado: 'Pago no banco e também recebido por fora',
  baixado_com_cobranca_aberta: 'Baixado no banco com a cobrança em aberto',
};

export function descreverTitulo(titulo: TituloBancario | null | undefined): { rotulo: string; tom: Tom; detalhe: string | null } {
  if (!titulo) return { ...ESTADOS.sem_titulo, detalhe: null };
  const base = ESTADOS[titulo.estado] ?? ESTADOS.sem_titulo;
  const rotulo = titulo.canal === 'sicredi_pix'
    ? ({ em_registro: 'Pix Automático reservado', registrado: 'Pix Automático agendado', baixado: 'Agendamento Pix encerrado' } as Record<string, string>)[titulo.estado] ?? base.rotulo
    : base.rotulo;
  const partes: string[] = [];
  if (titulo.canal && BANCO_DO_CANAL[titulo.canal]) partes.push(BANCO_DO_CANAL[titulo.canal]);
  if (titulo.nosso_numero) partes.push(`${titulo.canal === 'sicredi_pix' ? 'transação' : 'nosso número'} ${titulo.nosso_numero}`);
  if (titulo.baixa_status === 'pendente') partes.push('baixa PENDENTE no banco');
  if (titulo.baixa_status === 'solicitada') partes.push('baixa enviada ao banco, aguardando confirmação');
  if (titulo.baixa_status === 'confirmada') partes.push('baixa confirmada');
  if (titulo.pendencia) partes.push(ROTULO_PENDENCIA[titulo.pendencia] ?? titulo.pendencia);
  const tom: Tom = titulo.pendencia || titulo.baixa_status === 'pendente' ? 'warning' : base.tom;
  return { rotulo, tom, detalhe: partes.length ? partes.join(' · ') : null };
}

/** Título registrado pela API do Sicredi (ações próprias: consulta e baixa pela API). */
export function ehSicredi(titulo: TituloBancario | null | undefined): boolean {
  return titulo?.canal === 'sicredi_api';
}

export function ehPixAutomatico(titulo: TituloBancario | null | undefined): boolean {
  return titulo?.canal === 'sicredi_pix';
}

/** Consultar o desfecho só faz sentido quando o registro está pendente. */
export function podeConsultarDesfecho(titulo: TituloBancario | null | undefined): boolean {
  return !!titulo && (titulo.estado === 'desfecho_desconhecido' || titulo.estado === 'em_registro');
}
