'use client';

import { useEffect, useState } from 'react';

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
          // Token válido, só sem permissão para ESTA página — não é motivo
          // pra derrubar a sessão inteira. Antes isto fazia logout completo
          // só por clicar num item de menu errado (gerava ticket de suporte
          // à toa); o backend já protege de verdade via require_roles.
          setUser(me);
          setError('Acesso restrito a este perfil.');
          return;
        }
        setUser(me);
      })
      .catch((err) => {
        // 401 (sessão expirada/inválida) já tentou renovar sozinho dentro do
        // apiFetch; só chega aqui se o refresh também falhou — aí sim a
        // sessão acabou de verdade e o logout() revoga no servidor. 403 é
        // igual ao caso acima (token válido, sem permissão): não desloga,
        // só sinaliza. Qualquer outro erro (rede, 429, 5xx) também não
        // desloga — uma falha transitória não pode jogar o usuário pro login.
        const status = (err as { status?: number })?.status;
        if (status === 401) {
          logout(loginPath);
          return;
        }
        if (status === 403) {
          setError('Acesso restrito a este perfil.');
          return;
        }
        setError(err instanceof Error ? err.message : 'Não foi possível carregar a sessão.');
      })
      .finally(() => setLoading(false));
  }, [allowedRoles.join('|'), loginPath]);

  return { token, user, loading, error };
}
