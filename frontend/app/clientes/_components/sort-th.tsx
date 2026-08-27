import type { ClientSort, ClientSortField } from './types';

export function SortTh({
  field, label, sort, onSort, className,
}: {
  field: ClientSortField;
  label: string;
  sort: ClientSort;
  onSort: (f: ClientSortField) => void;
  className?: string;
}) {
  const active = sort.field === field;
  return (
    <th
      onClick={() => onSort(field)}
      className={`cursor-pointer select-none px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-700 dark:hover:text-slate-300 ${className ?? ''}`}
    >
      {label}{' '}
      <span className={active ? 'text-brand-600 dark:text-brand-400' : 'text-slate-300 dark:text-slate-600'}>
        {active ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </th>
  );
}
