'use client';

import { createContext, useContext, useEffect, useId, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

/**
 * Registro de ações contextuais do Assistente de Ações — permite que
 * qualquer página/aba (ex.: os-detalhes-tab.tsx, financeiro/page.tsx)
 * publique um punhado de ações ligadas às SUAS PRÓPRIAS closures já
 * existentes (changeStatus, handleReceive, openSwapTracker...), sem a
 * paleta (global-search.tsx) precisar conhecer a implementação de cada
 * tela. Ver plano "Assistente de Ações" para o racional completo.
 */
export interface AssistantConfirmSpec {
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
}

export interface ContextualAction {
  id: string;
  label: string;
  icon?: LucideIcon;
  keywords?: string[];
  run: () => void | Promise<void>;
  confirm?: AssistantConfirmSpec;
  /**
   * Marque quando `run` já tem seu próprio gate nativo (window.confirm/
   * window.prompt) que pode abortar silenciosamente (ex.: deleteVehicle,
   * handleCancel do financeiro). Sem isso, a paleta aguardaria a Promise e
   * mostraria "concluído com sucesso" mesmo quando o usuário cancelou o
   * prompt nativo e nada aconteceu. Com `manualFeedback`, a paleta só fecha
   * e dispara `run` — o próprio fluxo existente (confirm/prompt + feedback
   * da página) continua sendo a fonte da verdade, igual ao botão real.
   */
  manualFeedback?: boolean;
}

interface AssistantContextValue {
  register: (ownerId: string, actions: ContextualAction[]) => void;
  unregister: (ownerId: string) => void;
  subscribe: (listener: () => void) => () => void;
  getActions: () => ContextualAction[];
}

const AssistantContext = createContext<AssistantContextValue | null>(null);

export function AssistantContextProvider({ children }: { children: ReactNode }) {
  const registryRef = useRef<Map<string, ContextualAction[]>>(new Map());
  const cachedRef = useRef<ContextualAction[]>([]);
  const listenersRef = useRef<Set<() => void>>(new Set());

  const value = useMemo<AssistantContextValue>(() => {
    function recomputeAndNotify() {
      cachedRef.current = Array.from(registryRef.current.values()).flat();
      listenersRef.current.forEach((listener) => listener());
    }
    return {
      register(ownerId, actions) {
        registryRef.current.set(ownerId, actions);
        recomputeAndNotify();
      },
      unregister(ownerId) {
        if (registryRef.current.delete(ownerId)) recomputeAndNotify();
      },
      subscribe(listener) {
        listenersRef.current.add(listener);
        return () => listenersRef.current.delete(listener);
      },
      // Referência estável entre chamadas (só muda quando register/unregister
      // roda) — necessário para useSyncExternalStore não entrar em loop.
      getActions: () => cachedRef.current,
    };
  }, []);

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}

function useAssistantContextValue(): AssistantContextValue {
  const ctx = useContext(AssistantContext);
  if (!ctx) {
    throw new Error(
      'useAssistantContextActions/useAssistantAvailableActions precisa estar dentro de <AssistantContextProvider> (montado em PageShell).',
    );
  }
  return ctx;
}

/**
 * Publica uma lista de ações contextuais enquanto o componente chamador
 * estiver montado (ex.: um modal de detalhe aberto). Remove automaticamente
 * no unmount — evita ações "fantasmas" de uma tela/registro que não está
 * mais aberto.
 */
export function useAssistantContextActions(actions: ContextualAction[]): void {
  const { register, unregister } = useAssistantContextValue();
  const ownerId = useId();

  useEffect(() => {
    register(ownerId, actions);
    return () => unregister(ownerId);
    // `actions` é recriado a cada render do chamador — isso é intencional:
    // garante que `run` sempre feche sobre o estado mais recente da página.
  }, [register, unregister, ownerId, actions]);
}

/** Consumido só pela paleta (global-search.tsx) para listar as ações contextuais disponíveis agora. */
export function useAssistantAvailableActions(): ContextualAction[] {
  const { subscribe, getActions } = useAssistantContextValue();
  return useSyncExternalStore(subscribe, getActions, getActions);
}
