import './globals.css';
import type { Metadata, Viewport } from 'next';
import { QueryProvider } from '@/components/query-provider';
import { ThemeProvider } from '@/components/theme-provider';
import { FloatingThemeToggle } from '@/components/theme-toggle';

export const viewport: Viewport = {
  themeColor: '#FFB800',
};

export const metadata: Metadata = {
  title: { default: 'MasterSat', template: '%s | MasterSat' },
  description: 'Solução completa em Rastreamento — sistema de gestão para empresa de rastreamento veicular',
  manifest: '/site.webmanifest',
  icons: {
    icon: [
      { url: '/logo.png', type: 'image/png' },
      { url: '/favicon.svg', type: 'image/svg+xml' },
    ],
    apple: { url: '/logo.png', type: 'image/png' },
    shortcut: '/logo.png',
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: 'MasterSat',
  },
  formatDetection: { telephone: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <body>
        <QueryProvider>
          <ThemeProvider>
            <FloatingThemeToggle />
            {children}
          </ThemeProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
