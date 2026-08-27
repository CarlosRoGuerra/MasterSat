'use client';

import { useQuery } from '@tanstack/react-query';

import { apiFetch } from './api';
import { getAccessToken } from './auth';
import type { AuthUser } from './domain-types';

/**
 * Usuário logado, para uso fora do fluxo de guarda de rota (ex.: filtrar o
 * menu lateral por role). Independente do useAuthGuard de cada página — o
 * cache do React Query (staleTime abaixo) evita bater em /auth/me de novo a
 * cada navegação entre telas dentro da mesma sessão.
 */
export function useCurrentUser() {
  const token = getAccessToken();
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => apiFetch<AuthUser>('/auth/me', {}, token),
    enabled: !!token,
    staleTime: 60_000,
  });
}
