// Minimal NEAR JSON-RPC client (server only). Treat all replies as untrusted.
import 'server-only'

export type Fetch = typeof fetch

export interface Rpc {
  /** 'full' | 'function_call' | null (no such key / account). */
  accessKeyKind(accountId: string, publicKey: string): Promise<'full' | 'function_call' | null>
  viewAccount(accountId: string): Promise<{ amount: string; locked: string; storage_usage: number } | null>
  view<T = unknown>(contract: string, method: string, args: object): Promise<T>
}

export function createRpc(url: string, f: Fetch = fetch): Rpc {
  async function query(params: object): Promise<Record<string, unknown>> {
    const res = await f(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 'ctb', method: 'query', params: { finality: 'final', ...params } }),
      signal: AbortSignal.timeout(10_000),
    })
    if (!res.ok) throw new Error(`rpc http ${res.status}`)
    const j = (await res.json()) as { result?: Record<string, unknown>; error?: unknown }
    if (j.error) throw Object.assign(new Error('rpc error'), { rpcError: j.error })
    if (!j.result || typeof j.result !== 'object') throw new Error('rpc: no result')
    return j.result
  }
  const notFound = (e: unknown) => JSON.stringify((e as { rpcError?: unknown }).rpcError ?? '').match(/UNKNOWN_(ACCESS_KEY|ACCOUNT)/)

  return {
    async accessKeyKind(account_id, public_key) {
      let r: Record<string, unknown>
      try {
        r = await query({ request_type: 'view_access_key', account_id, public_key })
      } catch (e) {
        if (notFound(e)) return null
        throw e
      }
      if (typeof r.error === 'string') return null // older nodes put errors in result
      if (r.permission === 'FullAccess') return 'full'
      if (r.permission && typeof r.permission === 'object') return 'function_call'
      return null
    },
    async viewAccount(account_id) {
      try {
        const r = await query({ request_type: 'view_account', account_id })
        return { amount: String(r.amount), locked: String(r.locked), storage_usage: Number(r.storage_usage) }
      } catch (e) {
        if (notFound(e)) return null
        throw e
      }
    },
    async view(account_id, method_name, args) {
      const r = await query({
        request_type: 'call_function',
        account_id,
        method_name,
        args_base64: Buffer.from(JSON.stringify(args)).toString('base64'),
      })
      if (!Array.isArray(r.result)) throw new Error('rpc: bad view result')
      return JSON.parse(Buffer.from(r.result as number[]).toString('utf8'))
    },
  }
}
