import { ReactNode } from 'react';
export function Card({ children }: { children: ReactNode }) { return <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">{children}</div>; }
