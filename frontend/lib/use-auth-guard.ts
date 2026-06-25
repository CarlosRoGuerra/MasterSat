'use client';

import { useEffect, useState } from 'react';

import { apiFetch } from '@/lib/api';
import { AuthUser, clearSession, getAccessToken } from '@/lib/auth';

type AllowedRole = AuthUser['role'];

type GuardState = {
  token: string;
  user: AuthUser | null;
  loading: boolean;
  error: string;
};

export function useAuthGuard(allowedRoles: AllowedRole[], loginPath: string): GuardState {
  const [token, setToken] = useState('');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const currentToken = getAccessToken();
    if (!currentToken) {
      window.location.href = loginPath;
      return;
    }

    setToken(currentToken);

    apiFetch<AuthUser>('/auth/me', {}, currentToken)
      .then((me) => {
        if (!allowedRoles.includes(me.role)) {
          clearSession();
          window.location.href = '/login/admin';
          return;
        }
        setUser(me);
      })
      .catch((err) => {
        // 401 (sessão expirada/ inválida) já é tratado dentro do apiFetch:
        // limpa os tokens e redireciona para o login. Aqui só chegam erros
        // que NÃO são de autenticação (rede, 429 de rate limit, 5xx). Nesse
        // caso NÃO deslogar — senão uma falha transitória joga o usuário pra
        // tela de login. Apenas sinaliza o erro; o token continua válido.
        const status = (err as { status?: number })?.status;
        if (status === 401 || status === 403) {
          clearSession();
          window.location.href = loginPath;
          return;
        }
        setError(err instanceof Error ? err.message : 'Não foi possível carregar a sessão.');
      })
      .finally(() => setLoading(false));
  }, [allowedRoles.join('|'), loginPath]);

  return { token, user, loading, error };
}
