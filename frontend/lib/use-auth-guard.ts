'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { apiFetch, logout } from '@/lib/api';
import { AuthUser, getAccessToken } from '@/lib/auth';

type AllowedRole = AuthUser['role'];

type GuardState = {
  token: string;
  user: AuthUser | null;
  loading: boolean;
  error: string;
};

export function useAuthGuard(allowedRoles: AllowedRole[], loginPath: string): GuardState {
  const [token, setToken] = useState('');

  useEffect(() => {
    const currentToken = getAccessToken();
    if (!currentToken) {
      window.location.href = loginPath;
      return;
    }
    setToken(currentToken);
  }, [loginPath]);

  // O menu lateral usa a mesma query em useCurrentUser. O cache compartilha
  // a validação entre os componentes e evita repeti-la em cada troca de tela.
  const { data: user = null, error: queryError, isPending } = useQuery({
    queryKey: ['auth', 'me', token],
    queryFn: () => apiFetch<AuthUser>('/auth/me', {}, token),
    enabled: !!token,
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    if ((queryError as { status?: number } | null)?.status === 401) logout(loginPath);
  }, [queryError, loginPath]);

  const status = (queryError as { status?: number } | null)?.status;
  const error = user && !allowedRoles.includes(user.role)
    ? 'Acesso restrito a este perfil.'
    : status === 403
      ? 'Acesso restrito a este perfil.'
      : queryError && status !== 401
        ? queryError instanceof Error ? queryError.message : 'Não foi possível carregar a sessão.'
        : '';

  return { token, user, loading: !token || isPending, error };
}
