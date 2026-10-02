import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { apiFetch, logout } from './api';
import { useAuthGuard } from './use-auth-guard';
import { useCurrentUser } from './use-current-user';

vi.mock('./api', () => ({ apiFetch: vi.fn(), logout: vi.fn() }));
vi.mock('./auth', () => ({ getAccessToken: () => 'test-token' }));

function Probe() {
  const guard = useAuthGuard(['admin'], '/login/admin');
  const current = useCurrentUser();
  return <span>{guard.user?.name ?? 'aguardando'} / {current.data?.name ?? 'aguardando'}</span>;
}

function RestrictedProbe() {
  const guard = useAuthGuard(['admin'], '/login/admin');
  return <span>{guard.error || 'permitido'}</span>;
}

describe('validação compartilhada da sessão', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(logout).mockReset();
  });

  it('reutiliza /auth/me no menu e ao trocar de página', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ id: 1, name: 'Teste', email: 'teste@exemplo.com', role: 'admin' } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ui = () => <QueryClientProvider client={client}><Probe /></QueryClientProvider>;

    const first = render(ui());
    await screen.findByText('Teste / Teste');
    expect(apiFetch).toHaveBeenCalledTimes(1);

    first.unmount();
    render(ui());
    await waitFor(() => expect(screen.getByText('Teste / Teste')).toBeTruthy());
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(logout).not.toHaveBeenCalled();
  });

  it('mantém a sessão quando o perfil não pode abrir a página', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ id: 2, name: 'Financeiro', email: 'f@exemplo.com', role: 'financeiro' } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={client}><RestrictedProbe /></QueryClientProvider>);

    await screen.findByText('Acesso restrito a este perfil.');
    expect(logout).not.toHaveBeenCalled();
  });
});
