/**
 * Casamento difuso local para o Assistente de Ações — não é a busca de
 * entidades (essa continua batendo em GET /search no backend). Usado só
 * contra listas pequenas e estáticas (catálogo de ações + itens de
 * navegação), então roda 100% no cliente, sem debounce/API.
 *
 * Pontuação: prefixo da string inteira > prefixo de alguma palavra > apenas
 * substring em algum lugar. Ignora acento e caixa para tolerar "veiculo" vs
 * "veículo", "os" vs "OS", etc.
 */
function normalize(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

export function fuzzyScore(query: string, candidates: string[]): number {
  const q = normalize(query);
  if (!q) return 0;

  let best = 0;
  for (const raw of candidates) {
    const candidate = normalize(raw);
    if (!candidate) continue;

    if (candidate === q) {
      best = Math.max(best, 100);
      continue;
    }
    if (candidate.startsWith(q)) {
      best = Math.max(best, 80);
      continue;
    }
    const wordStart = candidate.split(/\s+/).some((word) => word.startsWith(q));
    if (wordStart) {
      best = Math.max(best, 60);
      continue;
    }
    if (candidate.includes(q)) {
      best = Math.max(best, 40);
    }
  }
  return best;
}

export function fuzzyMatches(query: string, candidates: string[]): boolean {
  return fuzzyScore(query, candidates) > 0;
}
