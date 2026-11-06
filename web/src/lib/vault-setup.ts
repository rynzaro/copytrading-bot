// Vault setup: the owner-signed transactions that create the trading wallet.
// STUB: ticket 0004 decides the account-creation flow ("Account creation (open)" in docs/interfaces/vault.md).
// Until it lands this module assumes a sub-account `vault.<owner>` created in one owner-signed batch.
// Everything about that assumption lives here so 0004's result replaces only this file.
import { DCL, WRAP, isAccountId, nearToYocto } from './near'
import { call, totalDeposit, type PlannedTx } from './tx'

export const GAS_ALLOWANCE_YOCTO = nearToYocto('1')

/** Estimates, not measured. 0004 replaces them. */
export const SETUP_COSTS = {
  account_storage: nearToYocto('0.05'),
  wrap_storage: nearToYocto('0.00125'),
  dcl_storage: nearToYocto('0.1'),
}

export function vaultIdFor(ownerId: string): string | null {
  if (!isAccountId(ownerId)) return null
  const id = `vault.${ownerId}`
  return isAccountId(id) ? id : null
}

export type SetupPlan = { vault_id: string; txs: PlannedTx[]; cost_yocto: string }

export function buildSetupPlan(p: { ownerId: string; botPublicKey: string; codeHash: string }): SetupPlan {
  const vault = vaultIdFor(p.ownerId)
  if (!vault) throw new Error('owner account name too long for a vault sub-account')
  if (!/^ed25519:[1-9A-HJ-NP-Za-km-z]{43,44}$/.test(p.botPublicKey)) throw new Error('bad bot key')
  if (!/^[1-9A-HJ-NP-Za-km-z]{43,44}$/.test(p.codeHash)) throw new Error('bad code hash')
  const txs: PlannedTx[] = [
    {
      receiver_id: vault,
      label: 'Create trading wallet',
      actions: [
        { kind: 'create_account' },
        { kind: 'transfer', deposit: SETUP_COSTS.account_storage },
        { kind: 'use_global_contract', code_hash: p.codeHash },
        call('new', { owner_id: p.ownerId }, 30),
        call('set_bot_key', { public_key: p.botPublicKey, allowance: GAS_ALLOWANCE_YOCTO }, 30, '1'),
      ],
    },
    {
      receiver_id: WRAP,
      label: 'Register wNEAR',
      actions: [call('storage_deposit', { account_id: vault, registration_only: true }, 10, SETUP_COSTS.wrap_storage)],
    },
    {
      receiver_id: DCL,
      label: 'Register Rhea DCL',
      actions: [call('storage_deposit', { account_id: vault, registration_only: true }, 10, SETUP_COSTS.dcl_storage)],
    },
  ]
  return { vault_id: vault, txs, cost_yocto: totalDeposit(txs).toString() }
}
