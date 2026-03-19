import './globals.css';
import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'Rastreamento ERP', description: 'Sistema de gestão para empresa de rastreamento veicular' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="pt-BR"><body>{children}</body></html>; }
