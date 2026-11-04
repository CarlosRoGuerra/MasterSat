import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch }));
import { useSicrediDisponivel } from './use-sicredi-disponivel';

describe('disponibilidade do Sicredi para escolhas bancárias', () => {
  beforeEach(() => { apiFetch.mockReset(); });

  it.each([
    { habilitado: false },
    { habilitado: true, erro_configuracao: 'Chave não configurada' },
    undefined,
  ])('mantém a opção oculta quando o status não libera a emissão: %s', async status => {
    apiFetch.mockResolvedValue(status);
    const { result } = renderHook(() => useSicrediDisponivel('token'));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/sicredi/status', {}, 'token'));
    expect(result.current).toBe(false);
  });

  it('libera apenas a resposta configurada, desabilitando ao fechar o fluxo', async () => {
    apiFetch.mockResolvedValue({ habilitado: true, erro_configuracao: null });
    const { result, rerender } = renderHook(({ ativo }) => useSicrediDisponivel('token', ativo), { initialProps: { ativo: true } });
    await waitFor(() => expect(result.current).toBe(true));
    rerender({ ativo: false });
    expect(result.current).toBe(false);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('mantém a opção oculta após erro de consulta', async () => {
    apiFetch.mockImplementation(async () => { throw new Error('Sem conexão'); });
    const { result } = renderHook(() => useSicrediDisponivel('token'));
    await act(async () => {});
    expect(result.current).toBe(false);
  });
});
