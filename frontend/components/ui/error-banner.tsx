'use client';

import { AlertTriangle } from 'lucide-react';

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="flex items-start gap-3 rounded-md px-4 py-3 text-sm"
      style={{ background: '#FCEBEB', borderLeft: '3px solid #E24B4A' }}
    >
      <AlertTriangle className="mt-0.5 h-[18px] w-[18px] shrink-0" style={{ color: '#A32D2D' }} />
      <p style={{ color: '#A32D2D' }}>{message}</p>
    </div>
  );
}
