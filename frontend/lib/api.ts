export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function buildApiUrl(path: string) {
  return `${API_URL.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const response = await fetch(buildApiUrl(path), {
    ...options,
    headers: {
      ...(!isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    cache: 'no-store',
  });

  if (!response.ok) {
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
    throw new Error(message);
  }

  return response.json();
}
