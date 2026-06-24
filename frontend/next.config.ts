import type { NextConfig } from 'next';
// output: 'standalone' → build mínimo para Docker (estágio "runner" do Dockerfile).
const nextConfig: NextConfig = { reactStrictMode: true, output: 'standalone' };
export default nextConfig;
