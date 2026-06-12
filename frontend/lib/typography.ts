/**
 * Sistema tipográfico unificado do MasterSat.
 * Importe as constantes necessárias em vez de hardcodar classes Tailwind.
 *
 * Uso:
 *   import { ty } from '@/lib/typography';
 *   <p className={ty.label}>Clientes</p>
 *   <p className={ty.kpi}>248</p>
 */

export const ty = {
  /** Rótulo de seção / label de card  — 11px, uppercase, tracking wide, slate-400 */
  label: 'text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 dark:text-slate-500',

  /** Valor de KPI principal — 28px, peso médio, sem leading extra */
  kpi: 'text-[28px] font-medium leading-none tabular-nums',

  /** Subtexto de KPI / hint — 12px regular, slate-400 */
  kpiSub: 'text-[12px] text-slate-400 dark:text-slate-500',

  /** Cabeçalho de tabela (Th) — 11px, uppercase, tracking, slate-400 */
  tableHeader: 'text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 dark:text-slate-500',

  /** Célula de tabela (Td) — 13px regular, slate-700 */
  tableCell: 'text-[13px] text-slate-700 dark:text-slate-300',

  /** Título de card / seção em texto — 13px semibold */
  cardTitle: 'text-[13px] font-semibold text-slate-900 dark:text-white',

  /** Texto de corpo secundário — 13px regular, slate-500 */
  body: 'text-[13px] text-slate-500 dark:text-slate-400',

  /** Badge / pill — 11px semibold */
  badge: 'text-[11px] font-medium',
} as const;
