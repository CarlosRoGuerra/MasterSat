export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function buildApiUrl(path: string) {
  return `${API_URL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

function loginPathForCurrentPage() {
  if (typeof window !== 'undefined' && window.location.pathname.startsWith('/cliente')) {
    return '/login/cliente';
  }
  return '/login/admin';
}

// Single-flight: vários 401 simultâneos disparam UM refresh só; os demais aguardam.
let refreshPromise: Promise<string | null> | null = null;

// O refresh token não passa mais por aqui: vive num cookie httpOnly (setado
// pelo backend via Set-Cookie em /auth/login e /auth/refresh), então o
// próprio navegador o envia com credentials:'include' — o JS nunca lê nem
// grava esse valor. Sem isto o refresh token era só localStorage: XSS
// exfiltrava e um invasor ficava com acesso válido por até 7 dias.
async function refreshAccessToken(): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const resp = await fetch(buildApiUrl('/auth/refresh'), {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
        });
        if (!resp.ok) return null;
        const data = await resp.json();
        if (!data?.access_token) return null;
        localStorage.setItem('access_token', data.access_token);
        return data.access_token as string;
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
export async function logout(loginPath: string = '/login/admin'): Promise<void> {
  if (typeof window === 'undefined') return;
  try {
    await fetch(buildApiUrl('/auth/logout'), {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
    });
  } catch {
    // Falha de rede não pode travar o logout local — a sessão local é
    // limpa de qualquer forma; o refresh token expira sozinho no pior caso.
  }
  localStorage.removeItem('access_token');
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
    const newToken = await refreshAccessToken();
    if (newToken) response = await doFetch(newToken);
  }

  if (!response.ok) {
    // effectiveToken só existe quando a chamada pretendia usar uma sessão já
    // aberta. Sem ele (ex.: o POST de /auth/login em si), um 401 é só
    // "credenciais inválidas" — nunca sessão expirada, e não deve redirecionar
    // para o login nem sobrescrever a mensagem de erro do backend.
    if (response.status === 401 && effectiveToken && typeof window !== 'undefined') {
      // Refresh indisponível ou também expirado → sessão realmente encerrada
      localStorage.removeItem('access_token');
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
        message = data.detail.map((item: any) => item?.msg || JSON.stringify(item)).join(' | ');
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
