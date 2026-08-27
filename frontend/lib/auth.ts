export type AuthUser = {
  id: number;
  name: string;
  email: string;
  role: 'admin' | 'operacional' | 'financeiro' | 'cliente';
  client_id?: number | null;
};

// O refresh token não passa mais por aqui: vive num cookie httpOnly setado
// pelo backend (Set-Cookie em /auth/login e /auth/refresh) — o JS nunca tem
// acesso a ele, nem pra ler nem pra gravar. Só o access token (30 min) fica
// em localStorage.
export function saveSession(accessToken: string) {
  if (typeof window === 'undefined') return;
  localStorage.setItem('access_token', accessToken);
}

// Limpeza local (sem chamada ao backend). Prefira logout() abaixo para um
// "Sair" de verdade — clearSession() sozinho não revoga o refresh token no
// servidor, só derruba o access token deste dispositivo/aba.
export function clearSession() {
  if (typeof window === 'undefined') return;
  localStorage.removeItem('access_token');
}

export function getAccessToken() {
  if (typeof window === 'undefined') return '';
  return localStorage.getItem('access_token') || '';
}

export function redirectByRole(user: AuthUser) {
  if (typeof window === 'undefined') return;
  if (user.role === 'cliente') {
    window.location.href = '/cliente/dashboard';
    return;
  }
  window.location.href = '/dashboard';
}
