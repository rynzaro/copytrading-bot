import { describe, expect, it } from 'vitest'
import { SETUP_YOCTO, buildSetupPlan, ownerKind, setupBlocker, vaultIdFor } from './vault-setup'
import { minOut, sellRoute, sellTx, withdrawTokenTx } from './owner-txs'
import { nearToYocto } from './near'
import { gas } from './tx'

const KEY = 'ed25519:' + '1'.repeat(44)
const HASH = '2'.repeat(44)

describe('vault setup plan (contracts/vault/README.md "Account creation")', () => {
  it('one owner tx: create, fund, global code by hash, init, bot key; no AddKey', () => {
    const p = buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: HASH })
    expect(p.vault_id).toBe('copytrade.alice.near')
    expect(p.txs).toHaveLength(1)
    expect(p.txs[0].receiver_id).toBe('copytrade.alice.near')
    expect(p.txs[0].actions).toEqual([
      { kind: 'create_account' },
      { kind: 'transfer', deposit: SETUP_YOCTO },
      { kind: 'use_global_contract', code_hash: HASH },
      { kind: 'function_call', method: 'new', args: { owner_id: 'alice.near' }, gas: gas(20), deposit: '0' },
      {
        kind: 'function_call',
        method: 'set_bot_key',
        args: { public_key: KEY, allowance: nearToYocto('1') },
        gas: gas(50),
        deposit: '1',
      },
    ])
    expect(JSON.stringify(p)).not.toMatch(/add_key|deploy_contract/)
  })
  it('setup cost is the sum of measured parts, under 1 NEAR; funds ride along but are not cost', () => {
    expect(SETUP_YOCTO).toBe(nearToYocto('0.06125'))
    const p = buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: HASH, fundsYocto: nearToYocto('10') })
    expect(p.txs[0].actions[1]).toEqual({ kind: 'transfer', deposit: nearToYocto('10.06125') })
    expect(p.cost_yocto).toBe((BigInt(SETUP_YOCTO) + 1n).toString())
    expect(BigInt(p.cost_yocto)).toBeLessThan(BigInt(nearToYocto('1')))
    expect(p.funds_yocto).toBe(nearToYocto('10'))
  })
  it('rejects bad inputs', () => {
    expect(() => buildSetupPlan({ ownerId: 'alice.near', botPublicKey: 'x', codeHash: HASH })).toThrow()
    expect(() => buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: 'x' })).toThrow()
    expect(() => buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: HASH, fundsYocto: '-1' })).toThrow()
    expect(vaultIdFor('a'.repeat(60) + '.near')).toBeNull()
  })
  it('implicit owners get a "use a named account" message', () => {
    const hex = 'ab'.repeat(32)
    expect(ownerKind(hex)).toBe('implicit')
    expect(ownerKind('0x' + 'ab'.repeat(20))).toBe('eth_implicit')
    expect(vaultIdFor(hex)).toBeNull()
    expect(setupBlocker(hex)).toMatch(/implicit account.*named account/s)
    expect(setupBlocker('0x' + 'ab'.repeat(20))).toMatch(/named account/)
    expect(() => buildSetupPlan({ ownerId: hex, botPublicKey: KEY, codeHash: HASH })).toThrow(/named account/)
    expect(setupBlocker('a'.repeat(52) + '.near')).toMatch(/too long/)
    expect(setupBlocker('alice.near')).toBeNull()
    expect(ownerKind('a'.repeat(49) + '.near')).toBe('named') // copytrade. + 54 = 64
  })
})

describe('owner txs', () => {
  it('min_out from slippage', () => {
    expect(minOut('10000', 100)).toBe('9900')
    expect(minOut('10000', 'unlimited')).toBe('1')
    expect(minOut('1', 9999)).toBe('1')
    expect(() => minOut('10000', null)).toThrow()
  })
  it('sell route only for direct wNEAR pools', () => {
    expect(sellRoute({ venue: 'dcl', pool_id: 'abc.near|wrap.near|2000' }, 'abc.near')?.hop).toEqual({
      pool_id: 'abc.near|wrap.near|2000',
      token_in: 'abc.near',
      token_out: 'wrap.near',
    })
    expect(sellRoute({ venue: 'dcl', pool_id: 'abc.near|token.rhealab.near|2000' }, 'abc.near')).toBeNull()
    expect(sellRoute({ venue: 'ref_v1', pool_id: '123' }, 'abc.near')?.venue).toBe('ref_v1')
    expect(sellRoute({ venue: 'intear', pool_id: '1' }, 'abc.near')).toBeNull()
  })
  it('owner calls attach 1 yocto; sell must end in wNEAR', () => {
    const w = withdrawTokenTx('vault.alice.near', 'abc.near')
    expect(w.actions[0]).toMatchObject({ method: 'withdraw_token', deposit: '1' })
    expect(() =>
      sellTx('v', { venue: 'dcl', hops: [{ pool_id: 'p', token_in: 'a', token_out: 'b' }], amount_in: '1', min_out: '1' }),
    ).toThrow()
  })
})
