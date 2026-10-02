import type { NextConfig } from 'next';
// output: 'standalone' → build mínimo para Docker (estágio "runner" do Dockerfile).
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  // A lockfile outside this project must not make standalone tracing walk
  // protected directories in the user's home folder.
  outputFileTracingRoot: process.cwd(),
};
export default nextConfig;
