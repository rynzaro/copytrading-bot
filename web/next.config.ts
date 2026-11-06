import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'standalone', // deploy/web.Dockerfile runs .next/standalone/server.js
  serverExternalPackages: ['better-sqlite3'],
  poweredByHeader: false,
}

export default nextConfig
