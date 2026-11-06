// Test harness: in-memory DB, fake RPC and worker, request helpers.
import { openDb } from '@/server/db'
import { NonceStore } from '@/server/nonce'
import { createApi } from '@/server/api'
import { createSession } from '@/server/session'
import { ensureUser } from '@/server/repo'
import type { Rpc } from '@/server/rpc'
import type { Worker } from '@/server/worker'

export const HOST = 'copytrade.example'

export function harness(opts: { keys?: Record<string, 'full' | 'function_call'> } = {}) {
  const db = openDb(':memory:', { requireSchema: true })
  const nonces = new NonceStore(new Uint8Array(32).fill(9))
  const keys = opts.keys ?? {}
  const views: { contract: string; method: string; args: object }[] = []
  const rpc: Rpc = {
    async accessKeyKind(a, pk) {
      return keys[`${a}/${pk}`] ?? null
    },
    async viewAccount() {
      return { amount: '5000000000000000000000000', locked: '0', storage_usage: 1000 }
    },
    async view<T>(contract: string, method: string, args: object) {
      views.push({ contract, method, args })
      if (method === 'ft_balance_of') return '42' as T
      if (method === 'ft_metadata') return { symbol: 'BOB<script>', decimals: 18 } as T
      if (method === 'quote') return { amount: '10000' } as T
      if (method === 'get_return') return '10000' as T
      return {} as T
    },
  }
  const worker: Worker & { calls: string[] } = {
    calls: [],
    async botKey(o) {
      this.calls.push(`bot-key ${o}`)
      return { public_key: 'ed25519:' + '1'.repeat(44) }
    },
    async vaultConfirmed(o, v) {
      this.calls.push(`confirm ${o} ${v}`)
      db.prepare('UPDATE users SET vault_id = ? WHERE owner_id = ?').run(v, o)
      return { ok: true }
    },
  }
  const api = createApi({ db, nonces, rpc, worker, appHost: HOST, secureCookies: true })

  function login(owner: string) {
    ensureUser(db, owner)
    return createSession(db, owner).token
  }

  function req(method: string, path: string, o: { token?: string; body?: unknown; origin?: string | null } = {}) {
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (o.token) headers.cookie = `ctb_session=${o.token}`
    if (o.origin !== null && method !== 'GET') headers.origin = o.origin ?? `https://${HOST}`
    return api(
      new Request(`https://${HOST}/api${path}`, {
        method,
        headers,
        body: o.body === undefined ? undefined : JSON.stringify(o.body),
      }),
    )
  }

  return { db, nonces, rpc, worker, api, login, req, views }
}
