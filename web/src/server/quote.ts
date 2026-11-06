// Sell quotes from pool views. Results are untrusted numbers: validated as digit strings.
import 'server-only'
import type { Rpc } from './rpc'
import { DCL, REF_V1 } from '@/lib/near'
import type { Hop, Venue } from '@/lib/owner-txs'

export async function quoteHop(rpc: Rpc, venue: Venue, hop: Hop, amountIn: string): Promise<string> {
  let out: unknown
  if (venue === 'dcl') {
    const r = await rpc.view<{ amount?: unknown }>(DCL, 'quote', {
      pool_ids: [hop.pool_id],
      input_token: hop.token_in,
      output_token: hop.token_out,
      input_amount: amountIn,
    })
    out = r?.amount
  } else {
    out = await rpc.view(REF_V1, 'get_return', {
      pool_id: Number(hop.pool_id),
      token_in: hop.token_in,
      amount_in: amountIn,
      token_out: hop.token_out,
    })
  }
  if (typeof out !== 'string' || !/^\d{1,40}$/.test(out) || out === '0') throw new Error('no quote')
  return out
}
