'use client';

import clsx from 'clsx';

/**
 * Campo do "dia de vencimento" (1 a 31).
 *
 * Existe para matar uma classe de bug que já apareceu em três telas: o input
 * era renderizado condicionalmente (`{valor ? <texto/> : <input/>}`), então ao
 * digitar o primeiro dígito o valor virava truthy, o campo SUMIA e era
 * impossível digitar o segundo — "10" travava em "1". Quem usa este componente
 * deve mantê-lo SEMPRE montado; o resumo textual fica ao lado, nunca no lugar.
 *
 * Também usa `type="text" + inputMode="numeric"` em vez de `type="number"`:
 * no Android o teclado numérico é o mesmo, mas sem os spinners e sem o
 * comportamento de devolver string vazia em estados intermediários.
 */
export function BillingDayInput({
  value,
  onChange,
  className,
  placeholder = 'Dia',
  autoFocus,
  disabled,
}: {
  value: string;
  onChange: (valor: string) => void;
  className?: string;
  placeholder?: string;
  autoFocus?: boolean;
  disabled?: boolean;
}) {
  return (
    <input
      type="text"
      inputMode="numeric"
      maxLength={2}
      placeholder={placeholder}
      value={value}
      autoFocus={autoFocus}
      disabled={disabled}
      aria-label="Dia do vencimento"
      onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 2))}
      className={clsx(
        'rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition',
        'placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20',
        'dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:placeholder:text-slate-400',
        className,
      )}
    />
  );
}

/** Dia válido para vencimento? Vazio conta como válido (campo opcional). */
export function diaVencimentoValido(valor: string): boolean {
  if (!valor.trim()) return true;
  const n = Number(valor);
  return Number.isInteger(n) && n >= 1 && n <= 31;
}

/** Mensagem de erro do dia, ou null se estiver ok. */
export function erroDiaVencimento(valor: string): string | null {
  return diaVencimentoValido(valor) ? null : 'Informe um dia entre 1 e 31.';
}
