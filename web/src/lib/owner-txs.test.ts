// Tx shape of every owner action (docs/interfaces/vault.md "Methods"). The same plans, written to
// contracts/vault/tests/fixtures/web_plans.json, are signed against the real vault in the sandbox e2e
// (contracts/vault/tests/e2e.rs). Update the fixture with `pnpm vitest run -u`.
import { describe, expect, it } from 'vitest'
import { depositTx, pauseTx, removeBotKeyTx, sellTx, setBotKeyTx, upgradeTx, withdrawNearTx, withdrawTokenTx } from './owner-txs'
import { buildSetupPlan, GAS_ALLOWANCE_YOCTO } from './vault-setup'
import { nearToYocto, WRAP } from './near'
import { gas, type PlannedTx } from './tx'

// Placeholders the e2e replaces: owner and bot key. Owner is a sandbox sub-account of test.near.
const OWNER = 'alice.test.near'
const V = 'copytrade.alice.test.near'
const KEY = 'ed25519:' + '1'.repeat(44)
const HASH = '2'.repeat(44)
const MEME = 'meme.near'

const fc = (method: string, args: object, tgas: number, deposit = '1') => ({
  kind: 'function_call',
  method,
  args,
  gas: gas(tgas),
  deposit,
})
const one = (t: PlannedTx) => {
  expect(t.receiver_id).toBe(V)
  expect(t.actions).toHaveLength(1)
  return t.actions[0]
}

const sellArgs = {
  venue: 'dcl' as const,
  hops: [{ pool_id: `${MEME}|${WRAP}|2000`, token_in: MEME, token_out: WRAP }],
  amount_in: nearToYocto('1'),
  min_out: '1',
}

describe('owner action tx shapes', () => {
  it('deposit: plain transfer to the vault', () => {
    expect(one(depositTx(V, nearToYocto('5')))).toEqual({ kind: 'transfer', deposit: nearToYocto('5') })
  })
  it('sell: vault.sell({args}), 1 yocto', () => {
    expect(one(sellTx(V, sellArgs))).toEqual(fc('sell', { args: sellArgs }, 300))
  })
  it('withdraw NEAR: amount or null (all minus storage)', () => {
    expect(one(withdrawNearTx(V, nearToYocto('1')))).toEqual(fc('withdraw_near', { amount: nearToYocto('1') }, 100))
    expect(one(withdrawNearTx(V, null))).toEqual(fc('withdraw_near', { amount: null }, 100))
  })
  it('withdraw token: all by default', () => {
    expect(one(withdrawTokenTx(V, MEME))).toEqual(fc('withdraw_token', { token_id: MEME, amount: null }, 100))
    expect(one(withdrawTokenTx(V, MEME, '7'))).toEqual(fc('withdraw_token', { token_id: MEME, amount: '7' }, 100))
  })
  it('pause / unpause', () => {
    expect(one(pauseTx(V, true))).toEqual(fc('pause', {}, 50))
    expect(one(pauseTx(V, false))).toEqual(fc('unpause', {}, 50))
  })
  it('refill and remove the bot key', () => {
    expect(one(setBotKeyTx(V, KEY, GAS_ALLOWANCE_YOCTO))).toEqual(
      fc('set_bot_key', { public_key: KEY, allowance: nearToYocto('1') }, 50),
    )
    expect(one(removeBotKeyTx(V))).toEqual(fc('remove_bot_key', {}, 50))
  })
  it('upgrade: one owner_upgrade call; the vault itself runs migrate in the same receipt', () => {
    expect(one(upgradeTx(V, HASH))).toEqual(fc('owner_upgrade', { code_hash: HASH }, 100))
  })
  it('upgrade refuses a malformed code hash', () => {
    for (const h of ['', 'abc', '0'.repeat(44), HASH + '2', 'ed25519:' + HASH]) expect(() => upgradeTx(V, h)).toThrow()
  })
  it('every owner call attaches exactly 1 yocto and at most 300 Tgas', () => {
    for (const t of [upgradeTx(V, HASH), sellTx(V, sellArgs), withdrawNearTx(V, null), withdrawTokenTx(V, MEME), pauseTx(V, true), setBotKeyTx(V, KEY, '1'), removeBotKeyTx(V)]) {
      const a = one(t)
      expect(a).toMatchObject({ kind: 'function_call', deposit: '1' })
      if (a.kind === 'function_call') expect(BigInt(a.gas)).toBeLessThanOrEqual(BigInt(gas(300)))
    }
  })
})

describe('sandbox e2e fixture', () => {
  it('matches contracts/vault/tests/fixtures/web_plans.json', async () => {
    const setup = buildSetupPlan({ ownerId: OWNER, botPublicKey: KEY, codeHash: HASH, fundsYocto: nearToYocto('10') })
    const steps: Record<string, PlannedTx> = {
      setup: setup.txs[0],
      deposit: depositTx(V, nearToYocto('5')),
      pause: pauseTx(V, true),
      unpause: pauseTx(V, false),
      refill: setBotKeyTx(V, KEY, GAS_ALLOWANCE_YOCTO),
      sell: sellTx(V, sellArgs),
      withdraw_token: withdrawTokenTx(V, MEME),
      withdraw_near: withdrawNearTx(V, nearToYocto('1')),
      remove_bot_key: removeBotKeyTx(V),
      withdraw_all: withdrawNearTx(V, null),
    }
    const fixture = { owner: OWNER, vault: V, bot_key: KEY, code_hash: HASH, steps }
    await expect(JSON.stringify(fixture, null, 2) + '\n').toMatchFileSnapshot('../../../contracts/vault/tests/fixtures/web_plans.json')
  })
})
