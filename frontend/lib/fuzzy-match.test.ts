import { describe, expect, it } from 'vitest';
import { fuzzyMatches, fuzzyScore } from './fuzzy-match';

describe('fuzzyScore', () => {
  it('dá a pontuação máxima para correspondência exata (ignorando caixa/acento)', () => {
    expect(fuzzyScore('veiculo', ['Veículo'])).toBe(100);
    expect(fuzzyScore('VEÍCULO', ['veiculo'])).toBe(100);
  });

  it('pontua alto quando o candidato começa com a query', () => {
    expect(fuzzyScore('nov', ['Novo cliente'])).toBeGreaterThanOrEqual(80);
  });

  it('pontua médio quando alguma palavra do candidato começa com a query', () => {
    expect(fuzzyScore('cli', ['Novo cliente'])).toBeGreaterThanOrEqual(60);
    expect(fuzzyScore('cli', ['Novo cliente'])).toBeLessThan(80);
  });

  it('pontua baixo quando a query só aparece como substring', () => {
    expect(fuzzyScore('lient', ['Novo cliente'])).toBeGreaterThan(0);
    expect(fuzzyScore('lient', ['Novo cliente'])).toBeLessThan(60);
  });

  it('retorna 0 quando não há nenhuma correspondência', () => {
    expect(fuzzyScore('xyz123', ['Novo cliente'])).toBe(0);
  });

  it('retorna 0 para query vazia', () => {
    expect(fuzzyScore('', ['Novo cliente'])).toBe(0);
    expect(fuzzyScore('   ', ['Novo cliente'])).toBe(0);
  });

  it('usa o melhor score entre múltiplos candidatos (label + sinônimos)', () => {
    // "carro" não é prefixo de "novo carro" inteiro, mas é prefixo da 2ª palavra.
    expect(fuzzyScore('carro', ['Novo veículo', 'novo carro'])).toBe(60);
  });
});

describe('fuzzyMatches', () => {
  it('é true quando o score é maior que zero', () => {
    expect(fuzzyMatches('cliente', ['Novo cliente'])).toBe(true);
  });

  it('é false quando nada casa', () => {
    expect(fuzzyMatches('zzz', ['Novo cliente'])).toBe(false);
  });
});
