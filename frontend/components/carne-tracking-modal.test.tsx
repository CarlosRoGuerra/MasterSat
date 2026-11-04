import { act, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn(), entregarArquivo: vi.fn() }));
vi.mock('@/lib/api', () => ({ API_URL: 'http://localhost/api/v1', apiFetch: mocks.apiFetch }));
vi.mock('@/lib/arquivo', () => ({ entregarArquivo: mocks.entregarArquivo }));
import { CarneTrackingModal, useCarneTracking } from './carne-tracking-modal';

const completo = { status: 'completed', total: 2, prontas: 2, parcelas: [] };

describe('acompanhamento dos carnês bancários', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['pdf']) }));
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('mantém Ailos como padrão, inclusive no registro complementar e no PDF legado', async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => path === '/ailos/carne/lote'
      ? { id: 7, ticket: 'abc', status: 'processing' } : path.endsWith('registrar-pendentes') ? {} : completo);
    const { result } = renderHook(() => useCarneTracking('token'));
    await act(async () => { await result.current.iniciar([1, 2]); });
    expect(mocks.apiFetch.mock.calls.map(([path]) => path)).toEqual([
      '/ailos/carne/lote', '/ailos/lotes/7/registrar-pendentes', '/ailos/lotes/abc',
    ]);
    expect(fetch).toHaveBeenCalledWith('http://localhost/api/v1/boletos/carne/7/pdf', expect.anything());
  });

  it('registra e consulta Sicredi, sem completar automaticamente nem recorrer a outro banco', async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => path === '/sicredi/carne/lote'
      ? { id: 7, ticket: 'abc', status: 'processing' } : completo);
    const completed = vi.fn();
    const { result } = renderHook(() => useCarneTracking('token', completed));
    await act(async () => { await result.current.iniciar([1, 2], 'sicredi'); });
    expect(mocks.apiFetch.mock.calls.map(([path]) => path)).toEqual(['/sicredi/carne/lote', '/sicredi/lotes/abc']);
    expect(fetch).toHaveBeenCalledWith('http://localhost/api/v1/boletos/carne/7/pdf?banco=sicredi', expect.anything());
    expect(completed).toHaveBeenCalledWith({ loteId: 7, total: 2, banco: 'sicredi' });
    expect(result.current.track).toMatchObject({ banco: 'sicredi', fase: 'completo' });
  });

  it('ao reabrir um lote não o registra novamente e mantém o banco nos retries individuais e em massa', async () => {
    mocks.apiFetch.mockRejectedValue(new Error('Consulta indisponível'));
    const { result } = renderHook(() => useCarneTracking('token'));
    await act(async () => { await result.current.abrirExistente(7, 'abc', 'sicredi'); });
    expect(result.current.track?.fase).toBe('aguardando-manual');
    mocks.apiFetch.mockResolvedValue(completo);
    await act(async () => { await result.current.registrarParcela(2); });
    await act(async () => { await result.current.registrarPendentes(); });
    expect(mocks.apiFetch.mock.calls.filter(([, options]) => options?.method === 'POST').map(([path]) => path))
      .toEqual(['/sicredi/lotes/7/parcelas/2/registrar', '/sicredi/lotes/7/registrar-pendentes']);
    expect(mocks.apiFetch.mock.calls.every(([path]) => path.startsWith('/sicredi/'))).toBe(true);
  });

  it('reabrir um carnê completo não inicia um download e o parcial exige escolha explícita', async () => {
    mocks.apiFetch.mockResolvedValue(completo);
    const { result } = renderHook(() => useCarneTracking('token'));
    await act(async () => { await result.current.abrirExistente(7, 'abc', 'sicredi'); });
    expect(fetch).not.toHaveBeenCalled();
    await act(async () => { await result.current.baixarPdf(7, 'sicredi', true); });
    expect(fetch).toHaveBeenCalledWith('http://localhost/api/v1/boletos/carne/7/pdf?banco=sicredi&parcial=true', expect.anything());
  });

  it('descarta resposta tardia de outro banco, mesmo quando os dois tickets coincidem', async () => {
    let resolver!: (value: unknown) => void;
    mocks.apiFetch.mockImplementation((path: string) => path.startsWith('/ailos/')
      ? new Promise(resolve => { resolver = resolve; }) : Promise.resolve(completo));
    const { result } = renderHook(() => useCarneTracking('token'));
    let ailos!: Promise<void>;
    act(() => { ailos = result.current.abrirExistente(7, 'igual', 'ailos'); });
    await act(async () => { await result.current.abrirExistente(7, 'igual', 'sicredi'); });
    await act(async () => { resolver({ status: 'processing', total: 99, prontas: 0, parcelas: [] }); await ailos; });
    expect(result.current.track).toMatchObject({ banco: 'sicredi', total: 2, fase: 'completo' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('apresenta falhas parciais imediatamente, sem emitir novamente nem baixar um carnê incompleto', async () => {
    mocks.apiFetch.mockResolvedValue({ status: 'partial', total: 2, prontas: 1, parcelas: [
      { billing_id: 1, numero_parcela: 1, status: 'registrado' },
      { billing_id: 2, numero_parcela: 2, status: 'erro', erro: 'Desfecho desconhecido: consulte antes de reenviar' },
    ] });
    const { result } = renderHook(() => useCarneTracking('token'));
    await act(async () => { await result.current.abrirExistente(7, 'abc', 'sicredi'); });
    expect(result.current.track).toMatchObject({ banco: 'sicredi', fase: 'aguardando-manual', prontas: 1, total: 2 });
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['sicredi', 'ailos'] as const)('orienta a confirmação incerta no Sicredi e preserva a apresentação Ailos: %s', async banco => {
    const explicacao = 'O boleto pode existir no banco. A nova tentativa consulta antes de reenviar.';
    mocks.apiFetch.mockResolvedValue({ status: 'partial', total: 2, prontas: 1, parcelas: [
      { billing_id: 2, numero_parcela: 2, vencimento: '2099-10-15', valor: 10, status: 'processando', erro: explicacao },
    ] });
    const { result } = renderHook(() => useCarneTracking('token'));
    await act(async () => { await result.current.abrirExistente(7, 'abc', banco); });
    render(<CarneTrackingModal carne={result.current} />);
    if (banco === 'sicredi') {
      expect(screen.getByText('Aguardando confirmação')).toBeInTheDocument();
      expect(screen.getByText(explicacao)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Consultar e tentar' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Retomar pendentes' })).toBeInTheDocument();
      expect(screen.queryByText('Não localizado')).not.toBeInTheDocument();
    } else {
      expect(screen.getByText('Não localizado')).toBeInTheDocument();
      expect(screen.queryByText(explicacao)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Gerar' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Gerar pendentes' })).toBeInTheDocument();
    }
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1);
  });
});
