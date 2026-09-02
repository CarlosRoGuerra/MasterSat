import clsx from 'clsx';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'brand';

const variants: Record<BadgeVariant, string> = {
  default: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  success: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400',
  warning: 'bg-amber-50  text-amber-700  dark:bg-amber-950/50  dark:text-amber-400',
  danger:  'bg-rose-50   text-rose-700   dark:bg-rose-950/50   dark:text-rose-400',
  info:    'bg-sky-50    text-sky-700    dark:bg-sky-950/50    dark:text-sky-400',
  brand:   'bg-brand-50  text-brand-700  dark:bg-brand-950/40  dark:text-brand-300',
};

export function Badge({
  children,
  variant = 'default',
  className,
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold',
        variants[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* Helpers para mapear strings de status em variantes */
export function statusVariant(status: string): BadgeVariant {
  const map: Record<string, BadgeVariant> = {
    ativo: 'success',
    active: 'success',
    instalado: 'success',
    aprovado: 'success',
    paga: 'success',
    concluida: 'success',
    inativo: 'default',
    em_estoque: 'info',
    em_manutencao: 'warning',
    manutencao: 'warning',
    pendente: 'warning',
    em_andamento: 'info',
    aberta: 'info',
    em_analise: 'info',
    enviado: 'info',
    inadimplente: 'danger',
    vencida: 'danger',
    cancelada: 'danger',
    cancelado: 'danger',
    rejeitado: 'danger',
    extraviado: 'danger',
    descartado: 'danger',
    suspenso: 'warning',
    reenvio_solicitado: 'warning',
    // Prioridade da Ordem de Serviço
    baixa: 'default',
    normal: 'info',
    alta: 'warning',
    urgente: 'danger',
  };
  return map[status] ?? 'default';
}

export function statusLabel(status: string): string {
  const map: Record<string, string> = {
    ativo: 'Ativo',
    inativo: 'Inativo',
    inadimplente: 'Inadimplente',
    suspenso: 'Suspenso',
    instalado: 'Instalado',
    em_estoque: 'Em estoque',
    em_manutencao: 'Manutenção',
    extraviado: 'Extraviado',
    descartado: 'Descartado',
    pendente: 'Pendente',
    paga: 'Paga',
    vencida: 'Vencida',
    cancelada: 'Cancelada',
    cancelado: 'Cancelado',
    aberta: 'Aberta',
    em_andamento: 'Em andamento',
    concluida: 'Concluída',
    aprovado: 'Aprovado',
    rejeitado: 'Rejeitado',
    enviado: 'Enviado',
    em_analise: 'Em análise',
    reenvio_solicitado: 'Reenvio solicitado',
    admin: 'Administrador',
    operacional: 'Operacional',
    financeiro: 'Financeiro',
    cliente: 'Cliente',
    baixa: 'Baixa',
    normal: 'Normal',
    alta: 'Alta',
    urgente: 'Urgente',
  };
  return map[status] ?? status;
}
