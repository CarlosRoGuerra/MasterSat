import type { Config } from 'tailwindcss';
export default {
  darkMode: 'class',
  content: ['./app/**/*.{js,ts,jsx,tsx,mdx}', './components/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#e8f3fa',
          100: '#c9dff0',
          200: '#93b8d8',
          300: '#5e92bf',
          400: '#2f70a6',
          500: '#184062',
          600: '#14395a',
          700: '#102d45',
          800: '#0e2538',
          900: '#0a1d2d',
          950: '#060f18',
        },
      },
      borderRadius: {
        card: '1.5rem',
        panel: '2rem',
        modal: '1.75rem',
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
