import { describe, expect, it } from 'vitest'
import { buildSetupPlan, vaultIdFor } from './vault-setup'
import { minOut, sellRoute, sellTx, withdrawTokenTx } from './owner-txs'
import { nearToYocto } from './near'

const KEY = 'ed25519:' + '1'.repeat(44)
const HASH = '2'.repeat(44)

describe('vault setup plan (stub until 0004)', () => {
  it('costs under 1 NEAR and adds the bot key as owner', () => {
    const p = buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: HASH })
    expect(p.vault_id).toBe('vault.alice.near')
    expect(BigInt(p.cost_yocto)).toBeLessThan(BigInt(nearToYocto('1')))
    const first = p.txs[0]
    expect(first.actions[0]).toEqual({ kind: 'create_account' })
    expect(first.actions.some((a) => a.kind === 'function_call' && a.method === 'set_bot_key')).toBe(true)
    expect(JSON.stringify(p)).not.toContain('add_key')
  })
  it('rejects bad inputs', () => {
    expect(() => buildSetupPlan({ ownerId: 'alice.near', botPublicKey: 'x', codeHash: HASH })).toThrow()
    expect(() => buildSetupPlan({ ownerId: 'alice.near', botPublicKey: KEY, codeHash: 'x' })).toThrow()
    expect(vaultIdFor('a'.repeat(60) + '.near')).toBeNull()
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
