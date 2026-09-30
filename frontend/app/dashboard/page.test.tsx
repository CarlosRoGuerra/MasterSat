import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import DashboardPage from './page';
import { apiFetch } from '@/lib/api';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, apiFetch: vi.fn() };
});
vi.mock('@/lib/use-auth-guard', () => ({
  useAuthGuard: () => ({ token: 'test-token', loading: false, error: '' }),
}));
vi.mock('@/components/page-shell', () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

const mockApiFetch = vi.mocked(apiFetch);

const base = {
  clients: { active: 7, inactive: 1, delinquent: 2, new_this_month: 1, new_prev_month: 0 },
  vehicles: { total: 9 },
  trackers: { installed: 6, stock: 3, maintenance: 0 },
  service_orders: { open: 1, in_progress: 2, completed: 3 },
  reference_date: '2026-09-30',
};

function responder(dashboard: unknown, delinquency: unknown) {
  mockApiFetch.mockImplementation(async (...args: unknown[]) => {
    const path = String(args[0] ?? '');
    if (path.startsWith('/dashboard')) return dashboard as never;
    if (path.startsWith('/delinquency/status')) return delinquency as never;
    return undefined as never;
  });
}

beforeEach(() => mockApiFetch.mockReset());

describe('DashboardPage — acesso financeiro por perfil (SEC-01)', () => {
  it('perfil sem acesso financeiro: não quebra e não mostra blocos financeiros', async () => {
    responder(
      { ...base, finance: null, upcoming_billings: [] },
      { clientes_inadimplentes: 2, cobrancas_vencidas: null, valor_total_vencido: null },
    );
    render(<DashboardPage />);

    // O card de receita dá lugar à contagem de inadimplentes (dado de cadastro)
    expect(await screen.findByText('Clientes inadimplentes')).toBeInTheDocument();
    expect(screen.queryByText('Receita do mês')).not.toBeInTheDocument();
    expect(screen.queryByText('Resumo do mês')).not.toBeInTheDocument();
    expect(screen.queryByText('Próximos vencimentos')).not.toBeInTheDocument();
    expect(screen.queryByText('Risco financeiro')).not.toBeInTheDocument();
    expect(screen.queryByText(/em aberto/)).not.toBeInTheDocument();
    expect(screen.getByText('2 cliente(s) inadimplente(s)')).toBeInTheDocument();
  });

  it('perfil financeiro: mantém receita, resumo e vencimentos', async () => {
    responder(
      {
        ...base,
        finance: {
          pending_count: 4, overdue_count: 1, received_month: 1234.5,
          received_prev_month: 1000, delta_received: 234.5, delta_pct: 23.5,
        },
        upcoming_billings: [
          { id: 1, client_name: 'Cliente X', amount: 99.9, due_date: '2026-10-02', days_until: 2 },
        ],
      },
      { clientes_inadimplentes: 2, cobrancas_vencidas: 1, valor_total_vencido: 99.9 },
    );
    render(<DashboardPage />);

    expect(await screen.findByText('Receita do mês')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Cliente X')).toBeInTheDocument());
    expect(screen.getByText('Resumo do mês')).toBeInTheDocument();
    expect(screen.getByText('Risco financeiro')).toBeInTheDocument();
    expect(screen.getByText(/em aberto/)).toBeInTheDocument();
  });
});
