import vitals from 'eslint-config-next/core-web-vitals'
import ts from 'eslint-config-next/typescript'

const config = [
  ...vitals,
  ...ts,
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'src/components/catalyst/**'] },
]

export default config
