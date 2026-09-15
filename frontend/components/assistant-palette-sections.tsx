'use client';

import clsx from 'clsx';

type IconComponent = React.ComponentType<{ className?: string }>;

/** Cabeçalho de seção da paleta (Ações/Navegação/Para esta tela) — mesmo estilo dos cabeçalhos de categoria da busca de entidades. */
export function PaletteSectionHeader({ title }: { title: string }) {
  return (
    <p className="px-3 pb-1 pt-2 text-3xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-600">
      {title}
    </p>
  );
}

/** Uma linha de ação/navegação da paleta — mesmo estilo visual das linhas de resultado de busca. */
export function PaletteActionRow({
  label,
  icon: Icon,
  active,
  onSelect,
  onHover,
}: {
  label: string;
  icon?: IconComponent;
  active: boolean;
  onSelect: () => void;
  onHover: () => void;
}) {
  return (
    <button
      type="button"
      onMouseEnter={onHover}
      onClick={onSelect}
      className={clsx(
        'flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left transition-colors',
        active ? 'bg-brand-50 dark:bg-brand-950/40' : 'hover:bg-slate-50 dark:hover:bg-slate-800/70',
      )}
    >
      {Icon && <Icon className="h-4 w-4 shrink-0 text-slate-400" />}
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900 dark:text-white">{label}</span>
    </button>
  );
}
