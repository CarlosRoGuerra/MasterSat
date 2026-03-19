import type { Config } from 'tailwindcss';
export default { content: ['./app/**/*.{js,ts,jsx,tsx,mdx}', './components/**/*.{js,ts,jsx,tsx,mdx}'], theme: { extend: { colors: { brand: { 500: '#184062', 700: '#102d45', 900: '#0a1d2d' } } } }, plugins: [] } satisfies Config;
