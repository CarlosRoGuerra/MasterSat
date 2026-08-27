import { dirname } from 'path';
import { fileURLToPath } from 'url';
import { FlatCompat } from '@eslint/eslintrc';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends('next/core-web-vitals', 'next/typescript', 'prettier'),
  {
    ignores: ['node_modules/**', '.next/**', 'coverage/**', 'next-env.d.ts'],
  },
  {
    // Código legado ainda não migrado: baixamos regras de "any" e afins para
    // warning aqui, mas mantemos error para tudo que for escrito daqui pra
    // frente (regra abaixo, por padrão do bloco anterior).
    files: [
      'app/clientes/**/*.{ts,tsx}',
      'app/financeiro/**/*.{ts,tsx}',
      'app/veiculos/**/*.{ts,tsx}',
      'app/notas-fiscais/**/*.{ts,tsx}',
      'app/rastreadores/**/*.{ts,tsx}',
      'app/fechamento/**/*.{ts,tsx}',
      'app/ordens-servico/**/*.{ts,tsx}',
      'app/relatorios/**/*.{ts,tsx}',
    ],
    rules: {
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
];

export default eslintConfig;
