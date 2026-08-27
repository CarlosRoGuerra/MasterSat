import type { AuthUser } from './domain-types';

export type { AuthUser };

// Cookie SEM privilégio nenhum — não é o token, não dá acesso a nada sozinho.
// Só existe pra o middleware (que roda no servidor/edge e não enxerga
// localStorage nem o cookie httpOnly do refresh, que é do domínio da API)
// saber "há uma sessão local" e redirecionar pro login ANTES de mandar o HTML
// da página protegida, evitando o flash de conteúdo que o redirect via
// useAuthGuard (só dispara depois de montar) não consegue evitar sozinho.
// Se alguém forjar esse cookie, o pior caso é passar pelo middleware e cair
// numa página que o useAuthGuard e a própria API vão barrar do mesmo jeito.
// Exportado para o middleware.ts ler o mesmo nome — nunca duplicar a string.
export const SESSION_HINT_COOKIE = 'ms_session';

function setSessionHint() {
  if (typeof document === 'undefined') return;
  const secure = window.location.protocol === 'https:' ? '; secure' : '';
  document.cookie = `${SESSION_HINT_COOKIE}=1; path=/; samesite=lax; max-age=${60 * 60 * 24 * 7}${secure}`;
}

function clearSessionHint() {
  if (typeof document === 'undefined') return;
  document.cookie = `${SESSION_HINT_COOKIE}=; path=/; max-age=0`;
}

// O refresh token não passa mais por aqui: vive num cookie httpOnly setado
// pelo backend (Set-Cookie em /auth/login e /auth/refresh) — o JS nunca tem
// acesso a ele, nem pra ler nem pra gravar. Só o access token (30 min) fica
// em localStorage.
export function saveSession(accessToken: string) {
  if (typeof window === 'undefined') return;
  localStorage.setItem('access_token', accessToken);
  setSessionHint();
}

// Limpeza local (sem chamada ao backend). Prefira logout() em lib/api.ts para
// um "Sair" de verdade — clearSession() sozinho não revoga o refresh token no
// servidor, só derruba o access token deste dispositivo/aba (e o sinalizador
// do middleware, que segue sempre a mesma sessão que o access token).
export function clearSession() {
  if (typeof window === 'undefined') return;
  localStorage.removeItem('access_token');
  clearSessionHint();
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
