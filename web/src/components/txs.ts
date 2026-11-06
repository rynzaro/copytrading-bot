'use client'
import type { PlannedTx } from '@/lib/tx'

/** Sign and send owner txs with the wallet of the logged-in owner. Refuses another account. */
export async function send(owner: string, txs: PlannedTx[]) {
  const w = await import('@/lib/wallet')
  const account = await w.connect()
  if (account !== owner) throw new Error(`Wallet is on ${account}, but you are logged in as ${owner}. Switch account in the wallet.`)
  return w.signAndSend(txs)
}
