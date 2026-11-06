// Owner-signed vault calls (docs/interfaces/vault.md). Each attaches 1 yocto.
import { ONE_YOCTO, WRAP } from './near'
import type { Slippage } from './settings'
import { call, type PlannedTx } from './tx'

export type Venue = 'dcl' | 'ref_v1'
export type Hop = { pool_id: string; token_in: string; token_out: string }
export type SwapArgs = { venue: Venue; hops: Hop[]; amount_in: string; min_out: string }

const tx = (vault: string, label: string, method: string, args: object, tgas = 50): PlannedTx => ({
  receiver_id: vault,
  label,
  actions: [call(method, args, tgas, ONE_YOCTO)],
})

export const sellTx = (vault: string, args: SwapArgs) => {
  if (args.hops.at(-1)?.token_out !== WRAP) throw new Error('sell must end in wNEAR')
  if (BigInt(args.min_out) <= 0n) throw new Error('min_out must be > 0')
  return tx(vault, 'Sell', 'sell', { args }, 300)
}
export const withdrawNearTx = (vault: string, amount: string | null) => tx(vault, 'Withdraw NEAR', 'withdraw_near', { amount }, 100)
export const withdrawTokenTx = (vault: string, token_id: string, amount: string | null = null) =>
  tx(vault, 'Withdraw token', 'withdraw_token', { token_id, amount }, 100)
export const pauseTx = (vault: string, paused: boolean) => tx(vault, paused ? 'Pause' : 'Resume', paused ? 'pause' : 'unpause', {})
export const setBotKeyTx = (vault: string, public_key: string, allowance: string) =>
  tx(vault, 'Refill bot key', 'set_bot_key', { public_key, allowance })
export const depositTx = (vault: string, yocto: string): PlannedTx => ({
  receiver_id: vault,
  label: 'Deposit',
  actions: [{ kind: 'transfer', deposit: yocto }],
})

/** min_out from a quote and the user's slippage. Unlimited -> 1 (vault requires > 0). */
export function minOut(quote: string, slippage: Slippage): string {
  if (slippage === null) throw new Error('set slippage first')
  if (slippage === 'unlimited') return '1'
  const q = BigInt(quote)
  const m = (q * BigInt(10_000 - slippage)) / 10_000n
  return (m > 0n ? m : 1n).toString()
}

/** Reverse a single-hop buy route into a sell route. Null if not a direct wNEAR pool. */
export function sellRoute(signal: { venue: string | null; pool_id: string | null }, token: string): { venue: Venue; hop: Hop } | null {
  if (!signal.pool_id || (signal.venue !== 'dcl' && signal.venue !== 'ref_v1')) return null
  if (signal.venue === 'dcl') {
    const [x, y] = signal.pool_id.split('|')
    if (![x, y].includes(WRAP) || ![x, y].includes(token)) return null
  } else if (!/^\d+$/.test(signal.pool_id)) return null
  return { venue: signal.venue, hop: { pool_id: signal.pool_id, token_in: token, token_out: WRAP } }
}
