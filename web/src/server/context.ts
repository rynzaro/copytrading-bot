// Process-wide API wiring from env. Values are config, never logged.
import 'server-only'
import { createHash, randomBytes } from 'node:crypto'
import { createApi } from './api'
import { getDb } from './db'
import { NonceStore } from './nonce'
import { createRpc } from './rpc'
import { createWorker } from './worker'

let api: ReturnType<typeof createApi> | null = null

export function getApi() {
  if (api) return api
  const prod = process.env.NODE_ENV === 'production'
  const appHost = process.env.APP_HOST ?? (prod ? '' : 'localhost:3000')
  if (!appHost) throw new Error('APP_HOST must be set (NEP-413 recipient)')
  // Without SESSION_SECRET nonces only survive this process (fine: they live 5 minutes). Used nonces are kept
  // in the DB either way, so single use survives a restart.
  const secret = process.env.SESSION_SECRET
    ? createHash('sha256').update(process.env.SESSION_SECRET).digest()
    : randomBytes(32)
  const db = getDb()
  api = createApi({
    db,
    nonces: new NonceStore(secret, db),
    rpc: createRpc(process.env.NEAR_RPC_URL ?? 'https://free.rpc.fastnear.com'),
    worker: createWorker(process.env.WORKER_URL ?? 'http://127.0.0.1:8700'),
    appHost,
    secureCookies: prod && process.env.INSECURE_COOKIES !== '1',
  })
  return api
}
