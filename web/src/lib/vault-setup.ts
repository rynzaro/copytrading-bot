// Vault setup: the one owner-signed transaction that creates the trading wallet.
// Flow: contracts/vault/README.md "Account creation" (ticket 0004). Sub-account `copytrade.<owner>`, created by the
// owner, so the host can't create, pre-initialise or squat it. No AddKey: the vault never has a full-access key.
// Code: global contract by hash (immutable), not by publisher account.
import { isAccountId, nearToYocto } from './near'
import { call, totalDeposit, type PlannedTx } from './tx'

export const VAULT_PREFIX = 'copytrade'
export const GAS_ALLOWANCE_YOCTO = nearToYocto('1')

/**
 * What the setup transfer pays for. Measured in the 0004 sandbox (tests/sandbox.rs `setup_storage_and_gas`):
 * the vault uses 357 bytes besides its code (state + bot key); global contract by hash adds the 32-byte hash.
 * Storage costs 1e19 yocto per byte, so ~0.004 NEAR; we send 0.01 for margin.
 */
export const SETUP_COSTS = {
  /** Storage stake: account, state, bot key, global contract hash. Locked while the vault exists. */
  account_storage: nearToYocto('0.01'),
  /** wNEAR registration, paid by the vault when the bot calls `register_token("wrap.near")` after setup. */
  wrap_storage: nearToYocto('0.00125'),
  /** The vault keeps this aside for bot gas: buys can't spend it (`SWAP_GAS_RESERVE`). */
  gas_reserve: nearToYocto('0.05'),
}
export const SETUP_YOCTO = Object.values(SETUP_COSTS)
  .reduce((s, v) => s + BigInt(v), 0n)
  .toString()

/** Gas the owner attaches (owner pays; unused gas is refunded). `set_bot_key` 50 Tgas as in the sandbox setup. */
const NEW_TGAS = 20
const SET_BOT_KEY_TGAS = 50

/** Bot gas per buy, measured in sandbox: ~27 Tgas burnt, ~0.003 NEAR from the vault balance. */
export const BOT_GAS_PER_BUY_NEAR = '0.003'

export type OwnerKind = 'named' | 'implicit' | 'eth_implicit' | 'too_long' | 'invalid'

/** Implicit accounts are already 64 (or 42) characters: `copytrade.<owner>` can't exist (max 64). */
export function ownerKind(ownerId: string): OwnerKind {
  if (/^[0-9a-f]{64}$/.test(ownerId)) return 'implicit'
  if (/^0x[0-9a-f]{40}$/.test(ownerId)) return 'eth_implicit'
  if (!isAccountId(ownerId)) return 'invalid'
  return isAccountId(`${VAULT_PREFIX}.${ownerId}`) ? 'named' : 'too_long'
}

/** Why this owner can't create a vault, as shown to the user. Null: setup works. */
export function setupBlocker(ownerId: string): string | null {
  switch (ownerKind(ownerId)) {
    case 'named':
      return null
    case 'implicit':
    case 'eth_implicit':
      return (
        'Your wallet is an implicit account (a long hex address). The trading wallet is a sub-account of your wallet, ' +
        'and implicit accounts cannot have sub-accounts. Use a named account (like alice.near) for now.'
      )
    case 'too_long':
      return `Your account name is too long: ${VAULT_PREFIX}.<your account> must fit in 64 characters. Use a shorter named account.`
    case 'invalid':
      return 'Not a valid NEAR account.'
  }
}

export function vaultIdFor(ownerId: string): string | null {
  return ownerKind(ownerId) === 'named' ? `${VAULT_PREFIX}.${ownerId}` : null
}

const BOT_KEY = /^ed25519:[1-9A-HJ-NP-Za-km-z]{43,44}$/
const CODE_HASH = /^[1-9A-HJ-NP-Za-km-z]{43,44}$/

export type SetupPlan = { vault_id: string; txs: PlannedTx[]; cost_yocto: string; funds_yocto: string; code_hash: string }

/**
 * One transaction, signer = owner, receiver = `copytrade.<owner>`: CreateAccount, Transfer (setup + funds),
 * UseGlobalContract(hash), new(owner_id), set_bot_key(bot key, 1 NEAR allowance). If any action fails, all revert.
 */
export function buildSetupPlan(p: { ownerId: string; botPublicKey: string; codeHash: string; fundsYocto?: string }): SetupPlan {
  const blocked = setupBlocker(p.ownerId)
  if (blocked) throw new Error(blocked)
  const vault = vaultIdFor(p.ownerId)!
  if (!BOT_KEY.test(p.botPublicKey)) throw new Error('bad bot key')
  if (!CODE_HASH.test(p.codeHash)) throw new Error('bad code hash')
  const funds = p.fundsYocto ?? '0'
  if (!/^\d+$/.test(funds)) throw new Error('bad funds amount')
  const txs: PlannedTx[] = [
    {
      receiver_id: vault,
      label: 'Create trading wallet',
      actions: [
        { kind: 'create_account' },
        { kind: 'transfer', deposit: (BigInt(SETUP_YOCTO) + BigInt(funds)).toString() },
        { kind: 'use_global_contract', code_hash: p.codeHash },
        call('new', { owner_id: p.ownerId }, NEW_TGAS),
        call('set_bot_key', { public_key: p.botPublicKey, allowance: GAS_ALLOWANCE_YOCTO }, SET_BOT_KEY_TGAS, '1'),
      ],
    },
  ]
  const cost = totalDeposit(txs) - BigInt(funds)
  return { vault_id: vault, txs, cost_yocto: cost.toString(), funds_yocto: funds, code_hash: p.codeHash }
}
