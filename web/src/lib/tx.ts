// Planned owner transactions: plain data, converted to wallet actions in src/lib/wallet.ts.
export type PlannedAction =
  | { kind: 'create_account' }
  | { kind: 'transfer'; deposit: string }
  | { kind: 'use_global_contract'; code_hash: string }
  | { kind: 'function_call'; method: string; args: object; gas: string; deposit: string }

export type PlannedTx = { receiver_id: string; label: string; actions: PlannedAction[] }

export const TGAS = 10n ** 12n
export const gas = (tgas: number) => (BigInt(tgas) * TGAS).toString()

export const call = (method: string, args: object, tgas: number, deposit = '0'): PlannedAction => ({
  kind: 'function_call',
  method,
  args,
  gas: gas(tgas),
  deposit,
})

/** Total NEAR (yocto) attached across txs. */
export function totalDeposit(txs: PlannedTx[]): bigint {
  let t = 0n
  for (const tx of txs)
    for (const a of tx.actions) if (a.kind === 'transfer' || a.kind === 'function_call') t += BigInt(a.deposit)
  return t
}
