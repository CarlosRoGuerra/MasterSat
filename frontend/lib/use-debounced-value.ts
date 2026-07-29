'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Espera o usuário parar de digitar antes de propagar o valor.
 *
 * As buscas das listagens são feitas no servidor. Sem isso, cada tecla vira uma
 * requisição — "MASTERSAT" dispararia 9 chamadas, e as respostas podem voltar
 * fora de ordem e mostrar o resultado de uma busca antiga.
 *
 * Uso:
 *   const buscaDebounced = useDebouncedValue(busca);
 *   useEffect(() => { carregar(); }, [buscaDebounced]);
 */
export function useDebouncedValue<T>(value: T, delay = 400): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);

  return debounced;
}

/**
 * `true` enquanto o valor ainda não "assentou" — serve para mostrar um
 * indicador de "digitando…" sem piscar a tabela inteira.
 */
export function useIsDebouncing<T>(value: T, debounced: T): boolean {
  return value !== debounced;
}

/**
 * Roda `efeito` quando as dependências mudam, MAS pula a primeira execução.
 *
 * As telas já carregam a lista no mount (efeito do token). Sem isso, ligar a
 * busca automática causaria uma segunda requisição idêntica logo na abertura.
 */
export function useEffectSkipFirst(efeito: () => void, deps: unknown[]): void {
  const primeira = useRef(true);
  useEffect(() => {
    if (primeira.current) {
      primeira.current = false;
      return;
    }
    efeito();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
