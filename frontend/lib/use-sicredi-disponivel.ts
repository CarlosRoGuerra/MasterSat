'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';

/** Falhas de configuração ou de consulta nunca liberam a emissão no Sicredi. */
export function useSicrediDisponivel(token: string | null, ativo = true) {
  const [habilitado, setHabilitado] = useState(false);
  useEffect(() => {
    let atual = true;
    setHabilitado(false);
    if (token && ativo) {
      void (async () => {
        try {
          const status = await apiFetch<{ habilitado: boolean; erro_configuracao?: string | null }>('/sicredi/status', {}, token);
          if (atual) setHabilitado(!!status?.habilitado && !status.erro_configuracao);
        } catch { if (atual) setHabilitado(false); }
      })();
    }
    return () => { atual = false; };
  }, [token, ativo]);
  return habilitado;
}
