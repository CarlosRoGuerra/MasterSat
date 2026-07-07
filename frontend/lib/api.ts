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

async function refreshAccessToken(): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return null;
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const resp = await fetch(buildApiUrl('/auth/refresh'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
          cache: 'no-store',
        });
        if (!resp.ok) return null;
        const data = await resp.json();
        if (!data?.access_token) return null;
        localStorage.setItem('access_token', data.access_token);
        if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
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
    if (response.status === 401 && typeof window !== 'undefined') {
      // Refresh indisponível ou também expirado → sessão realmente encerrada
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      window.location.href = loginPathForCurrentPage();
      throw new Error('Sessão expirada.');
    }

    let message = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === 'string') {
        message = data.detail;
      } else if (Array.isArray(data?.detail)) {
        message = data.detail.map((item: any) => item?.msg || JSON.stringify(item)).join(' | ');
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
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }

  return response.json();
}
