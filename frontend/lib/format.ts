export function onlyDigits(value: string) {
  return value.replace(/\D/g, '');
}

export function formatCurrency(value: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value ?? 0);
}

/** Data curta pt-BR a partir de "YYYY-MM-DD" ou ISO completo; "—" quando ausente. */
export function formatDate(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
  return d.toLocaleDateString('pt-BR');
}

export function formatCpfCnpj(value: string) {
  const digits = onlyDigits(value).slice(0, 14);
  if (digits.length <= 11) {
    return digits
      .replace(/(\d{3})(\d)/, '$1.$2')
      .replace(/(\d{3})(\d)/, '$1.$2')
      .replace(/(\d{3})(\d{1,2})$/, '$1-$2');
  }
  return digits
    .replace(/^(\d{2})(\d)/, '$1.$2')
    .replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3')
    .replace(/\.(\d{3})(\d)/, '.$1/$2')
    .replace(/(\d{4})(\d)/, '$1-$2');
}

export function formatPhone(value: string) {
  const digits = onlyDigits(value).slice(0, 11);
  if (digits.length <= 10) {
    return digits
      .replace(/^(\d{2})(\d)/, '($1) $2')
      .replace(/(\d{4})(\d)/, '$1-$2');
  }
  return digits
    .replace(/^(\d{2})(\d)/, '($1) $2')
    .replace(/(\d{5})(\d)/, '$1-$2');
}

export function formatZipCode(value: string) {
  const digits = onlyDigits(value).slice(0, 8);
  return digits.replace(/(\d{5})(\d)/, '$1-$2');
}

export function validatePassword(password: string) {
  return {
    minLength: password.length >= 8,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    number: /\d/.test(password),
    special: /[^A-Za-z0-9]/.test(password),
  };
}

/** Rótulo da periodicidade de cobrança a partir do intervalo em meses. */
export function intervalLabel(months?: number | null): string {
  const m = months || 1;
  return ({ 1: 'Mensal', 3: 'Trimestral', 6: 'Semestral', 12: 'Anual' } as Record<number, string>)[m]
    || `A cada ${m} meses`;
}

/**
 * Sufixo do preço de um plano: "/mês", "/ano", " a cada 3 meses".
 *
 * Planos trimestrais/semestrais/anuais eram exibidos como "/mês" em todas as
 * telas, dando ao operador e ao cliente uma previsão comercial errada.
 */
export function pricePeriodSuffix(months?: number | null): string {
  const m = months || 1;
  if (m === 1) return '/mês';
  if (m === 12) return '/ano';
  return ` a cada ${m} meses`;
}
