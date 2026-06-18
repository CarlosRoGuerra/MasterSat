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
