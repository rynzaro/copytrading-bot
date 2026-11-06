// Login: NEP-413 signed message + full-access-key check on chain.
import 'server-only'
import type { DB } from './db'
import { verifyNep413 } from './nep413'
import type { NonceStore } from './nonce'
import type { Rpc } from './rpc'
import { createSession } from './session'
import { ensureUser } from './repo'
import { isAccountId } from '@/lib/near'

export const LOGIN_MESSAGE = 'Log in to Copytrade'

export type LoginInput = { accountId: unknown; publicKey: unknown; signature: unknown; nonce: unknown }
export type LoginDeps = { db: DB; nonces: NonceStore; rpc: Rpc; recipient: string; now?: number }

export type LoginResult = { ok: true; owner: string; token: string; expiresAt: number } | { ok: false; error: string; status: number }

export async function login(i: LoginInput, d: LoginDeps): Promise<LoginResult> {
  const now = d.now ?? Date.now()
  const { accountId, publicKey, signature, nonce } = i
  if (!isAccountId(accountId) || typeof publicKey !== 'string' || typeof signature !== 'string' || typeof nonce !== 'string')
    return { ok: false, error: 'bad request', status: 400 }
  const nonceBytes = new Uint8Array(Buffer.from(nonce, 'base64'))
  if (nonceBytes.length !== 32) return { ok: false, error: 'bad nonce', status: 400 }
  if (!verifyNep413({ publicKey, signature, message: LOGIN_MESSAGE, nonce: nonceBytes, recipient: d.recipient }))
    return { ok: false, error: 'bad signature', status: 401 }
  // Consume after the signature check so garbage can't burn a user's nonce; before the RPC so it is single use.
  if (!d.nonces.consume(nonce, now)) return { ok: false, error: 'nonce expired or used', status: 401 }
  let kind: Awaited<ReturnType<Rpc['accessKeyKind']>>
  try {
    kind = await d.rpc.accessKeyKind(accountId, publicKey)
  } catch {
    return { ok: false, error: 'rpc unavailable', status: 503 }
  }
  if (kind !== 'full') return { ok: false, error: 'key is not a full-access key of this account', status: 401 }
  ensureUser(d.db, accountId, now)
  const s = createSession(d.db, accountId, now)
  return { ok: true, owner: accountId, token: s.token, expiresAt: s.expiresAt }
}
