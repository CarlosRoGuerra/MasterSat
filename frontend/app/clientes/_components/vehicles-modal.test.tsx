import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useClientVehiclesDetailedQuery } from './queries';
import { VehiclesModal } from './vehicles-modal';

const vehicle = { id: 720, client_id: 2, plate: 'OXD0A94', status: 'ativo', brand: 'LAND ROVER', model: 'DISCOVERY 4 HSE' };
const tracker = { id: 631, client_id: 1, vehicle_id: 720, imei: '869671078139498', brand: 'Suntech', model: 'ST 300' };

function Harness() {
  const query = useClientVehiclesDetailedQuery('test-token', 2);
  return <>
    <button onClick={() => void query.refetch()}>Atualizar dados</button>
    <VehiclesModal open clientName="RUBERVAL DA SILVA FILHO" loading={query.isLoading}
      vehicles={query.data ?? []} error={query.error?.message} onClose={() => {}} />
  </>;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  return client;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('veículos do cliente após troca de proprietário', () => {
  it('carrega todas as páginas e mostra o rastreador mesmo com o cliente antigo', async () => {
    const firstPage = Array.from({ length: 500 }, (_, index) => ({ ...vehicle, id: index + 1, plate: `VEICULO${index + 1}` }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      expect(url.searchParams.get('limit')).toBe('500');
      if (url.pathname.endsWith('/vehicles/')) {
        expect(url.searchParams.get('client_id')).toBe('2');
        return Response.json({ items: url.searchParams.get('skip') === '0' ? firstPage : [vehicle], total: 501 });
      }
      expect(url.pathname).toMatch(/\/trackers\/$/);
      expect(url.searchParams.get('vehicle_client_id')).toBe('2');
      expect(url.searchParams.has('client_id')).toBe(false);
      return Response.json({ items: [tracker], total: 1 });
    });
    vi.stubGlobal('fetch', fetchMock);
    mount();

    expect(await screen.findByText(tracker.imei)).toBeInTheDocument();
    expect(screen.getByText('OXD0A94')).toBeInTheDocument();
    expect(screen.getByText('Suntech ST 300')).toBeInTheDocument();
    expect(screen.getByText('Mostrando 501 registro(s)')).toBeInTheDocument();
    expect(screen.getByText(/Cliente do rastreador diferente do proprietário/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Conferir vínculo' })).toHaveAttribute('href', '/veiculos?focus=720');
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('mostra a falha de consulta em vez de esconder o rastreador', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      return url.pathname.endsWith('/vehicles/')
        ? Response.json({ items: [vehicle], total: 1 })
        : Response.json({ detail: 'Falha ao consultar os equipamentos.' }, { status: 503 });
    }));
    mount();

    expect(await screen.findByText('Não foi possível carregar os vínculos')).toBeInTheDocument();
    expect(screen.getByText('Falha ao consultar os equipamentos.')).toBeInTheDocument();
    expect(screen.queryByText('Nenhum veículo vinculado')).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('remove o aviso após reconciliar e atualizar sem perder o IMEI', async () => {
    let trackerClient = 1;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      return Response.json({ items: url.pathname.endsWith('/vehicles/') ? [vehicle] : [{ ...tracker, client_id: trackerClient }], total: 1 });
    }));
    mount();
    expect(await screen.findByRole('link', { name: 'Conferir vínculo' })).toBeInTheDocument();

    trackerClient = 2;
    await userEvent.setup().click(screen.getByRole('button', { name: 'Atualizar dados' }));
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Conferir vínculo' })).not.toBeInTheDocument());
    expect(screen.getByText(tracker.imei)).toBeInTheDocument();
  });
});
