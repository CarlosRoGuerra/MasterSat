'use client';

import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // As telas já mostram os dados assim que chegam; refetch automático
            // ao focar a aba surpreenderia o operador no meio de uma edição.
            refetchOnWindowFocus: false,
            // apiFetch já trata 401 (refresh de token) — uma 2ª tentativa cobre
            // só falha de rede transitória, sem martelar o backend em erro real.
            retry: 1,
            staleTime: 30_000,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      {children}
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  );
}
