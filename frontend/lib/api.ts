import { clearSession } from './auth';

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function buildApiUrl(path: string) {
  const [rawPath, query] = path.replace(/^\/+/, '').split('?');
  let cleanPath = rawPath.replace(/\/+$/, '');
  // Rotas de listagem/criação são sempre definidas com barra final no backend
  // (@router.get('/'), @router.post('/')) — um único segmento sem barra
  // ('clients', 'trackers'...) dispara um redirect 307 do FastAPI. Esse
  // redirect quebra o CORS no navegador (falha só lá, curl segue e "funciona"),
  // então evitamos o redirect de vez adicionando a barra aqui. Não mexe em
  // caminhos com mais de um segmento ('clients/123', 'billings/reports/...'),
  // que são rotas definidas sem barra final.
  if (cleanPath && !cleanPath.includes('/')) {
    cleanPath += '/';
  }
  const url = `${API_URL.replace(/\/+$/, '')}/${cleanPath}`;
  return query ? `${url}?${query}` : url;
}

function loginPathForCurrentPage() {
  if (typeof window !== 'undefined' && window.location.pathname.startsWith('/cliente')) {
    return '/login/cliente';
  }
  return '/login/admin';
}

// Single-flight: vários 401 simultâneos disparam UM refresh só; os demais aguardam.
let refreshPromise: Promise<string | null> | null = null;

// Quantas vezes insistir quando o backend responde 409 ao refresh.
const REFRESH_CONFLICT_RETRIES = 3;

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// O refresh token não passa mais por aqui: vive num cookie httpOnly (setado
// pelo backend via Set-Cookie em /auth/login e /auth/refresh), então o
// próprio navegador o envia com credentials:'include' — o JS nunca lê nem
// grava esse valor. Sem isto o refresh token era só localStorage: XSS
// exfiltrava e um invasor ficava com acesso válido por até 7 dias.
//
// Várias abas: o refresh token é de uso único (SEC-02). Se duas abas renovam
// juntas com o mesmo cookie, uma vence e a outra recebe 409 — sem perder a
// sessão. A perdedora espera um pouco: a vencedora grava o access novo no
// localStorage (compartilhado entre abas) e o navegador passa a ter o cookie
// novo; então ou reaproveita esse access, ou renova de novo com o cookie novo.
async function refreshAccessToken(failedToken?: string): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        for (let attempt = 0; attempt <= REFRESH_CONFLICT_RETRIES; attempt++) {
          const stored = localStorage.getItem('access_token');
          if (failedToken && stored && stored !== failedToken) return stored;

          const resp = await fetch(buildApiUrl('/auth/refresh'), {
            method: 'POST',
            credentials: 'include',
            cache: 'no-store',
          });
          if (resp.status === 409) {
            await wait(300 * (attempt + 1));
            continue;
          }
          if (!resp.ok) return null;
          const data = await resp.json();
          if (!data?.access_token) return null;
          localStorage.setItem('access_token', data.access_token);
          return data.access_token as string;
        }
        return null;
      } catch {
        return null;
      } finally {
        // Libera para um próximo ciclo de refresh (depois que os aguardantes resolverem)
        setTimeout(() => { refreshPromise = null; }, 0);
      }
    })();
  }
  return refreshPromise;
}

// Logout de verdade: revoga o refresh token no servidor (não só limpa o
// access token local) — sem isto, um cookie vazado antes do clique em "Sair"
// continuava válido normalmente até expirar sozinho.
//
// O logout encerra só ESTA sessão (as outras abas deste navegador compartilham
// a mesma sessão e caem junto; outros dispositivos continuam logados). O
// access token vai junto: o backend revoga a sessão dele mesmo se o cookie
// não chegar.
export async function logout(loginPath: string = '/login/admin'): Promise<void> {
  if (typeof window === 'undefined') return;
  const accessToken = localStorage.getItem('access_token');
  try {
    await fetch(buildApiUrl('/auth/logout'), {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
  } catch {
    // Falha de rede não pode travar o logout local — a sessão local é
    // limpa de qualquer forma; o refresh token expira sozinho no pior caso.
  }
  clearSession();
  window.location.href = loginPath;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;

  // O estado das páginas pode guardar um access token antigo (já renovado em
  // outra chamada/aba). O localStorage é a fonte de verdade da sessão.
  let effectiveToken = token;
  if (token && typeof window !== 'undefined') {
    effectiveToken = localStorage.getItem('access_token') || token;
  }

  const doFetch = (authToken?: string) =>
    fetch(buildApiUrl(path), {
      ...options,
      headers: {
        ...(!isFormData ? { 'Content-Type': 'application/json' } : {}),
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        ...(options.headers || {}),
      },
      // O cookie httpOnly do refresh token é escopado a /auth (ver backend) —
      // incluir credentials aqui não expõe nada a mais nas outras rotas, só
      // garante que o cookie vai junto quando o caminho for de fato /auth/*.
      credentials: 'include',
      cache: 'no-store',
    });

  let response = await doFetch(effectiveToken);

  // Access token expirou (30 min): renova com o refresh token (7 dias) e
  // repete a requisição — o usuário não é deslogado no meio do trabalho.
  if (response.status === 401 && effectiveToken && typeof window !== 'undefined') {
    const newToken = await refreshAccessToken(effectiveToken);
    if (newToken) response = await doFetch(newToken);
  }

  if (!response.ok) {
    // effectiveToken só existe quando a chamada pretendia usar uma sessão já
    // aberta. Sem ele (ex.: o POST de /auth/login em si), um 401 é só
    // "credenciais inválidas" — nunca sessão expirada, e não deve redirecionar
    // para o login nem sobrescrever a mensagem de erro do backend.
    if (response.status === 401 && effectiveToken && typeof window !== 'undefined') {
      // Refresh indisponível ou também expirado → sessão realmente encerrada
      clearSession();
      window.location.href = loginPathForCurrentPage();
      throw new Error('Sessão expirada.');
    }

    let message = `HTTP ${response.status}`;
    let detail: unknown;
    try {
      const data = await response.json();
      detail = data?.detail;
      if (typeof data?.detail === 'string') {
        message = data.detail;
      } else if (Array.isArray(data?.detail)) {
        message = data.detail
          .map((item: unknown) => (item as { msg?: string })?.msg || JSON.stringify(item))
          .join(' | ');
      } else if (data?.detail && typeof data.detail === 'object' && typeof data.detail.message === 'string') {
        // detail estruturado ({code, message, ...}) — mostra a mensagem legível
        message = data.detail.message;
      } else if (data?.detail) {
        message = JSON.stringify(data.detail);
      } else {
        message = JSON.stringify(data);
      }
    } catch {
      try {
        message = await response.text();
      } catch {
        message = `HTTP ${response.status}`;
      }
    }
    const error = new Error(message) as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = detail;
    throw error;
  }

  return response.json();
}

// Envelope de listagem paginada por skip/limit (ver BE-02 no backend) — só
// /clients, /trackers e /vehicles usam esse formato; os demais endpoints de
// listagem continuam devolvendo array puro (limitam um teto, não paginam de
// verdade) e não passam por aqui.
export interface Page<T> {
  items: T[];
  total: number;
}

export async function apiFetchList<T>(path: string, options: RequestInit = {}, token?: string): Promise<T[]> {
  const page = await apiFetch<Page<T>>(path, options, token);
  return page.items;
}

/** Percorre skip/limit até trazer todos os registros — `pageSize` não pode
 *  passar do `le=` do endpoint (clients: 300, vehicles/trackers: 500). */
export async function apiFetchAll<T>(path: string, token?: string, pageSize = 500): Promise<T[]> {
  const sep = path.includes('?') ? '&' : '?';
  const all: T[] = [];
  for (let skip = 0; ; skip += pageSize) {
    const page = await apiFetch<Page<T>>(`${path}${sep}skip=${skip}&limit=${pageSize}`, {}, token);
    all.push(...page.items);
    if (page.items.length < pageSize || all.length >= page.total) return all;
  }
}
