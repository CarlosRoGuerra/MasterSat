'use client';

import { AlertTriangle } from 'lucide-react';

/**
 * Alguns erros trazem a saída junto (ex.: o link da consulta pública quando o
 * serviço de DANFSE do governo cai). Como texto puro o operador teria que
 * copiar a URL na mão, então o banner transforma links em links.
 */
function comLinks(message: string) {
  return message.split(/(https?:\/\/[^\s]+)/g).map((parte, i) =>
    /^https?:\/\//.test(parte) ? (
      <a
        key={i}
        href={parte}
        target="_blank"
        rel="noopener noreferrer"
        className="underline underline-offset-2"
      >
        {parte}
      </a>
    ) : (
      parte
    ),
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="flex items-start gap-3 rounded-md px-4 py-3 text-sm"
      style={{ background: '#FCEBEB', borderLeft: '3px solid #E24B4A' }}
    >
      <AlertTriangle className="mt-0.5 h-[18px] w-[18px] shrink-0" style={{ color: '#A32D2D' }} />
      <p style={{ color: '#A32D2D' }}>{comLinks(message)}</p>
    </div>
  );
}
