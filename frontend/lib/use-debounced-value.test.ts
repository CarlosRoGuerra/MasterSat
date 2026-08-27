import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useDebouncedValue, useEffectSkipFirst, useIsDebouncing } from './use-debounced-value';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useDebouncedValue', () => {
  it('só propaga o valor novo depois do delay', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 400), {
      initialProps: { value: 'a' },
    });
    expect(result.current).toBe('a');

    rerender({ value: 'ab' });
    expect(result.current).toBe('a'); // ainda não passou o delay

    act(() => {
      vi.advanceTimersByTime(399);
    });
    expect(result.current).toBe('a');

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe('ab');
  });

  it('reinicia a contagem a cada mudança (só o último valor "vence")', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 400), {
      initialProps: { value: 'a' },
    });

    rerender({ value: 'ab' });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    rerender({ value: 'abc' });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    // 300ms desde a 2ª mudança: ainda não bateu 400ms para "abc"
    expect(result.current).toBe('a');

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe('abc');
  });
});

describe('useIsDebouncing', () => {
  it('é true enquanto o valor bruto ainda não alcançou o debounced', () => {
    expect(useIsDebouncing('abc', 'a')).toBe(true);
    expect(useIsDebouncing('abc', 'abc')).toBe(false);
  });
});

describe('useEffectSkipFirst', () => {
  it('não roda o efeito na montagem, só nas mudanças seguintes', () => {
    const efeito = vi.fn();
    const { rerender } = renderHook(({ dep }) => useEffectSkipFirst(efeito, [dep]), {
      initialProps: { dep: 1 },
    });
    expect(efeito).not.toHaveBeenCalled();

    rerender({ dep: 2 });
    expect(efeito).toHaveBeenCalledTimes(1);

    rerender({ dep: 3 });
    expect(efeito).toHaveBeenCalledTimes(2);
  });
});
