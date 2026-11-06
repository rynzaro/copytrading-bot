// Client for the worker's internal HTTP API (docs/interfaces/worker-api.md).
import 'server-only'
import type { Fetch } from './rpc'

export interface Worker {
  botKey(ownerId: string): Promise<{ public_key: string }>
  vaultConfirmed(ownerId: string, vaultId: string): Promise<{ ok: boolean }>
}

export function createWorker(base: string, f: Fetch = fetch): Worker {
  async function post<T>(path: string, body: object): Promise<T> {
    const res = await f(base.replace(/\/$/, '') + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(60_000),
    })
    if (!res.ok) throw new Error(`worker ${path}: ${res.status}`)
    return (await res.json()) as T
  }
  return {
    async botKey(owner_id) {
      const r = await post<{ public_key?: unknown }>('/bot-key', { owner_id })
      if (typeof r.public_key !== 'string' || !r.public_key.startsWith('ed25519:')) throw new Error('worker: bad key')
      return { public_key: r.public_key }
    },
    async vaultConfirmed(owner_id, vault_id) {
      const r = await post<{ ok?: unknown }>('/vault-confirmed', { owner_id, vault_id })
      return { ok: r.ok === true }
    },
  }
}
