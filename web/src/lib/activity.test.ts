import { describe, expect, it } from 'vitest'
import { describeActivity } from './activity'

const N = (n: string) => n + '0'.repeat(24)

describe('activity rendering (db.sql payload keys)', () => {
  it('renders each kind from its keys', () => {
    expect(describeActivity('buy_filled', { token_id: 'abc.near', amount_in: N('10'), tx_hash: 'H1' })).toMatchObject({
      title: 'Bought abc.near',
      detail: '10 NEAR',
      tx_hash: 'H1',
      tone: 'good',
    })
    expect(describeActivity('buy_failed', { token_id: 'abc.near', reason: 'price_guard', amount_in: N('5') })).toMatchObject({
      title: 'Buy failed: abc.near',
      detail: 'price_guard · 5 NEAR',
      tone: 'bad',
    })
    expect(describeActivity('buy_skipped', { token_id: 'abc.near', reason: 'mcap_unknown' }).detail).toBe('mcap_unknown')
    expect(describeActivity('storage_paid', { token_id: 'abc.near', amount: '1250000000000000000000' }).detail).toBe('0.00125 NEAR')
    expect(describeActivity('gas_80', { vault_id: 'v.alice.near' }).title).toMatch(/Gas allowance 80%/)
    expect(describeActivity('still_active', { vault_id: 'v.alice.near' }).title).toMatch(/still active/)
    expect(describeActivity('sell', { token_id: 'abc.near', amount_in: '5', amount_out: N('2') }).detail).toBe('2 NEAR')
    expect(describeActivity('withdraw', { token_id: 'wrap.near', amount: N('3') }).detail).toBe('3 NEAR')
    expect(describeActivity('withdraw', { token_id: 'abc.near', amount: '77' }).detail).toBe('77 raw units')
    expect(describeActivity('system', { text: 'Feed down' }).title).toBe('Feed down')
  })

  it('renders limit_order alerts, both stages, never as a buy', () => {
    const p = { target_id: 'simsey.near', token_id: 'b.near', pool_id: 'b.near|wrap.near|10000', amount_near: N('14'), price: 0.000249567, tx_hash: 'H2' }
    expect(describeActivity('limit_order', { ...p, stage: 'placed' })).toMatchObject({
      title: 'simsey.near placed a limit buy: b.near',
      detail: '14 NEAR · at 0.000249567 NEAR · no copy buy',
      tone: 'neutral',
      tx_hash: 'H2',
    })
    expect(describeActivity('limit_order', { ...p, stage: 'filled' }).title).toBe('Limit buy by simsey.near filled: b.near')
    expect(describeActivity('limit_order', { ...p, stage: 'weird', price: 'x' })).toMatchObject({
      title: 'simsey.near placed a limit buy: b.near',
      detail: '14 NEAR · no copy buy',
    })
    for (const price of [Infinity, -1, 0, NaN, true]) expect(describeActivity('limit_order', { ...p, price }).detail).not.toMatch(/at /)
    expect(describeActivity('limit_order', {}).title).toBe('? placed a limit buy: ?')
  })

  it('survives missing or hostile payloads', () => {
    expect(describeActivity('buy_filled', {}).title).toBe('Bought ?')
    expect(describeActivity('buy_filled', { token_id: 'x', amount_in: 'NaN' }).detail).toBe('')
    expect(describeActivity('weird', { a: 1 }).title).toBe('weird')
    expect(describeActivity('system', { text: 42 }).title).toBe('System')
    expect(describeActivity('buy_filled', { token_id: 'a'.repeat(500) }).title.length).toBeLessThan(120)
  })
})
