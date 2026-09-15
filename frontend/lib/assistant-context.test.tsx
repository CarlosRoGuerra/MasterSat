import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AssistantContextProvider, useAssistantContextActions, useAssistantAvailableActions, type ContextualAction } from './assistant-context';

function Publisher({ actions }: { actions: ContextualAction[] }) {
  useAssistantContextActions(actions);
  return null;
}

function Reader({ onRead }: { onRead: (actions: ContextualAction[]) => void }) {
  const actions = useAssistantAvailableActions();
  onRead(actions);
  return null;
}

describe('AssistantContextProvider', () => {
  it('lança um erro claro se usado fora do Provider', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Reader onRead={() => {}} />)).toThrow(/AssistantContextProvider/);
    consoleError.mockRestore();
  });

  it('publica e lê ações contextuais', () => {
    const seen: ContextualAction[][] = [];
    const action: ContextualAction = { id: 'a1', label: 'Ação 1', run: () => {} };

    render(
      <AssistantContextProvider>
        <Publisher actions={[action]} />
        <Reader onRead={(a) => seen.push(a)} />
      </AssistantContextProvider>,
    );

    const last = seen[seen.length - 1];
    expect(last.map((a) => a.id)).toEqual(['a1']);
  });

  it('combina ações de múltiplos publicadores simultâneos', () => {
    const seen: ContextualAction[][] = [];
    const a: ContextualAction = { id: 'a', label: 'A', run: () => {} };
    const b: ContextualAction = { id: 'b', label: 'B', run: () => {} };

    render(
      <AssistantContextProvider>
        <Publisher actions={[a]} />
        <Publisher actions={[b]} />
        <Reader onRead={(actions) => seen.push(actions)} />
      </AssistantContextProvider>,
    );

    const last = seen[seen.length - 1];
    expect(last.map((x) => x.id).sort()).toEqual(['a', 'b']);
  });

  it('remove as ações de um componente quando ele desmonta (sem vazar entre navegações)', () => {
    const seen: ContextualAction[][] = [];
    const action: ContextualAction = { id: 'a1', label: 'Ação 1', run: () => {} };

    const { rerender } = render(
      <AssistantContextProvider>
        <Publisher actions={[action]} />
        <Reader onRead={(a) => seen.push(a)} />
      </AssistantContextProvider>,
    );
    expect(seen[seen.length - 1].map((a) => a.id)).toEqual(['a1']);

    // Simula sair da tela que registrou a ação (ex.: fechar o modal de detalhe).
    rerender(
      <AssistantContextProvider>
        <Reader onRead={(a) => seen.push(a)} />
      </AssistantContextProvider>,
    );

    expect(seen[seen.length - 1]).toEqual([]);
  });

  it('atualiza a lista quando o publicador re-registra com ações diferentes (closures mais recentes)', () => {
    const seen: ContextualAction[][] = [];
    const runV1 = vi.fn();
    const runV2 = vi.fn();

    const { rerender } = render(
      <AssistantContextProvider>
        <Publisher actions={[{ id: 'x', label: 'X v1', run: runV1 }]} />
        <Reader onRead={(a) => seen.push(a)} />
      </AssistantContextProvider>,
    );
    expect(seen[seen.length - 1][0].label).toBe('X v1');

    rerender(
      <AssistantContextProvider>
        <Publisher actions={[{ id: 'x', label: 'X v2', run: runV2 }]} />
        <Reader onRead={(a) => seen.push(a)} />
      </AssistantContextProvider>,
    );

    const last = seen[seen.length - 1];
    expect(last).toHaveLength(1);
    expect(last[0].label).toBe('X v2');
    last[0].run();
    expect(runV2).toHaveBeenCalledTimes(1);
    expect(runV1).not.toHaveBeenCalled();
  });
});
