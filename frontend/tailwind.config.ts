import type { Config } from 'tailwindcss';
export default {
  darkMode: 'class',
  content: ['./app/**/*.{js,ts,jsx,tsx,mdx}', './components/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#fffbeb',
          100: '#fff3c4',
          200: '#ffe680',
          300: '#ffd840',
          400: '#ffc700',
          500: '#ffb800',
          600: '#e09f00',
          700: '#b87e00',
          800: '#8a5e00',
          900: '#5c3e00',
          950: '#2e1f00',
        },
      },
      fontSize: {
        // Escala complementar à padrão do Tailwind — cobre os tamanhos que já
        // eram usados no app via valores arbitrários (text-[Npx]), agora nomeados
        // e centralizados. Não sobrescreve xs/sm/base/lg (mantidos como estão).
        '3xs': ['0.625rem', { lineHeight: '0.875rem' }],   // 10px — eyebrow, rótulo de grupo
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],      // 11px — legendas, cabeçalho de tabela
        body: ['0.8125rem', { lineHeight: '1.25rem' }],    // 13px — texto corrente em cards/tabelas
        heading: ['1.375rem', { lineHeight: '1.75rem' }],  // 22px — título de página
        stat: ['1.875rem', { lineHeight: '1' }],            // 30px — valor de destaque (StatCard)
      },
      boxShadow: {
        card: '0 4px 24px -8px rgba(15,23,42,0.12)',
        panel: '0 8px 40px -16px rgba(15,23,42,0.18)',
        elevated: '0 16px 56px -24px rgba(15,23,42,0.22)',
      },
    },
  },
  plugins: [],
} satisfies Config;
