import { defineConfig } from 'vitest/config'
import path from 'node:path'

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
      'server-only': path.resolve(import.meta.dirname, 'src/test/empty.ts'),
    },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
