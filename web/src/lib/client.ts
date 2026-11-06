'use client'
// Browser-side API calls. Same-origin fetch: the session cookie rides along, Origin is set by the browser.
import type { Me, Position, TargetRow, ActivityRow } from '@/server/types'

export class ApiError extends Error {
  constructor(public status: number, message: string, public data: Record<string, unknown> = {}) {
    super(message)
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, typeof data.error === 'string' ? data.error : `HTTP ${res.status}`, data)
  return data as T
}

export type VaultInfo = {
  vault_id: string | null
  near?: string | null
  storage_usage?: number | null
  wnear?: string | null
  paused?: boolean | null
  bot_key?: string | null
}
export type PositionView = Position & { balance: string | null; symbol: string | null; decimals: number | null; can_sell: boolean }

export const api = {
  nonce: () => call<{ nonce: string; message: string; recipient: string }>('GET', '/auth/nonce'),
  login: (b: object) => call<{ owner_id: string }>('POST', '/auth/login', b),
  logout: () => call('POST', '/auth/logout'),
  me: () => call<Me>('GET', '/me'),
  saveSettings: (settings: Me['settings']) => call<Me>('PUT', '/settings', { settings }),
  bot: (on: boolean) => call<{ bot_on: boolean }>('POST', '/bot', { on }),
  targets: () => call<TargetRow[]>('GET', '/targets'),
  setTarget: (target_id: string, enabled: boolean) => call<TargetRow[]>('PUT', '/targets', { target_id, enabled }),
  removeTarget: (target_id: string) => call<TargetRow[]>('DELETE', '/targets', { target_id }),
  positions: () => call<PositionView[]>('GET', '/positions'),
  vault: () => call<VaultInfo>('GET', '/vault'),
  sellQuote: (token_id: string, amount: string) =>
    call<{ quote: string; args: import('./owner-txs').SwapArgs }>('POST', '/sell-quote', { token_id, amount }),
  activity: () => call<ActivityRow[]>('GET', '/activity'),
  telegramLink: () => call<{ code: string; expires_at: number }>('POST', '/telegram-link'),
  botKey: () => call<{ public_key: string; vault_id: string }>('POST', '/setup/bot-key'),
  confirmVault: (vault_id: string) => call<Me>('POST', '/setup/confirm', { vault_id }),
}
