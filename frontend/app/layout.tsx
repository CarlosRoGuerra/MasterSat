import './globals.css';
import type { Metadata } from 'next';
import { ThemeProvider } from '@/components/theme-provider';
import { FloatingThemeToggle } from '@/components/theme-toggle';
export const metadata: Metadata = { title: 'Rastreamento ERP', description: 'Sistema de gestão para empresa de rastreamento veicular' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="pt-BR" suppressHydrationWarning><body><ThemeProvider><FloatingThemeToggle />{children}</ThemeProvider></body></html>; }
