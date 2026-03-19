export type AuthUser = {
  id: number;
  name: string;
  email: string;
  role: 'admin' | 'operacional' | 'financeiro' | 'cliente';
  client_id?: number | null;
};

export function saveSession(accessToken: string, refreshToken: string) {
  if (typeof window === 'undefined') return;
  localStorage.setItem('access_token', accessToken);
  localStorage.setItem('refresh_token', refreshToken);
}

export function clearSession() {
  if (typeof window === 'undefined') return;
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
}

export function getAccessToken() {
  if (typeof window === 'undefined') return '';
  return localStorage.getItem('access_token') || '';
}

export function getRefreshToken() {
  if (typeof window === 'undefined') return '';
  return localStorage.getItem('refresh_token') || '';
}

export function redirectByRole(user: AuthUser) {
  if (typeof window === 'undefined') return;
  if (user.role === 'cliente') {
    window.location.href = '/cliente/dashboard';
    return;
  }
  window.location.href = '/dashboard';
}
