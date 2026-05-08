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
        clearSession();
        setError(err instanceof Error ? err.message : 'Sessão inválida');
        window.location.href = loginPath;
      })
      .finally(() => setLoading(false));
  }, [allowedRoles.join('|'), loginPath]);

  return { token, user, loading, error };
}
