import { describe, expect, it } from 'vitest'
import { DEFAULT_SETTINGS, botBlockers, parseSettings, validateSettings } from './settings'

const ok = () => structuredClone(DEFAULT_SETTINGS)

describe('settings validation', () => {
  it('defaults follow DECISIONS: S1 10 NEAR < $1M, S2 5 NEAR < $3M, no guard, no slippage', () => {
    expect(DEFAULT_SETTINGS.ladder).toEqual([
      { max_mcap_usd: 1_000_000, size_near: '10' },
      { max_mcap_usd: 3_000_000, size_near: '5' },
    ])
    expect(DEFAULT_SETTINGS.price_guard_x).toBeNull()
    expect(DEFAULT_SETTINGS.slippage_bps).toBeNull()
    expect(validateSettings(ok()).ok).toBe(true)
  })

  it('accepts slippage as bps or "unlimited"', () => {
    expect(validateSettings({ ...ok(), slippage_bps: 100 }).ok).toBe(true)
    expect(validateSettings({ ...ok(), slippage_bps: 'unlimited' }).ok).toBe(true)
  })

  it.each([0, -1, 1.5, 10001, 'lots', true])('rejects slippage %s', (v) => {
    expect(validateSettings({ ...ok(), slippage_bps: v }).ok).toBe(false)
  })

  it('price guard: null or a positive finite number', () => {
    expect(validateSettings({ ...ok(), price_guard_x: 1.2 }).ok).toBe(true)
    for (const v of [0, -1, Infinity, '1.2', NaN]) expect(validateSettings({ ...ok(), price_guard_x: v }).ok).toBe(false)
  })

  it('ladder: 1..5 bands, ascending mcap, positive decimal NEAR sizes', () => {
    expect(validateSettings({ ...ok(), ladder: [] }).ok).toBe(false)
    expect(validateSettings({ ...ok(), ladder: [{ max_mcap_usd: 2e6, size_near: '1' }, { max_mcap_usd: 1e6, size_near: '1' }] }).ok).toBe(false)
    expect(validateSettings({ ...ok(), ladder: [{ max_mcap_usd: 1e6, size_near: '0' }] }).ok).toBe(false)
    expect(validateSettings({ ...ok(), ladder: [{ max_mcap_usd: 1e6, size_near: '1e3' }] }).ok).toBe(false)
    expect(validateSettings({ ...ok(), ladder: [{ max_mcap_usd: 1e6, size_near: 10 }] }).ok).toBe(false)
    expect(validateSettings({ ...ok(), ladder: [{ max_mcap_usd: 1e6, size_near: '2.5' }] }).ok).toBe(true)
    expect(validateSettings({ ...ok(), ladder: Array(6).fill(0).map((_, i) => ({ max_mcap_usd: (i + 1) * 1e6, size_near: '1' })) }).ok).toBe(false)
  })

  it('min mcap must be below the top band', () => {
    expect(validateSettings({ ...ok(), min_mcap_usd: 5e6 }).ok).toBe(false)
    expect(validateSettings({ ...ok(), min_mcap_usd: -1 }).ok).toBe(false)
    expect(validateSettings({ ...ok(), min_mcap_usd: 1e5 }).ok).toBe(true)
  })

  it('notify: ntfy topic charset, booleans, no unknown keys', () => {
    expect(validateSettings({ ...ok(), notify: { ...ok().notify, ntfy_topic: 'my_topic-123' } }).ok).toBe(true)
    expect(validateSettings({ ...ok(), notify: { ...ok().notify, ntfy_topic: '../x' } }).ok).toBe(false)
    expect(validateSettings({ ...ok(), notify: { ...ok().notify, still_active_weekly: 'yes' } }).ok).toBe(false)
    expect(validateSettings({ ...ok(), extra: 1 }).ok).toBe(false)
  })

  it('notify.limit_orders: boolean, default on, missing means on', () => {
    expect(DEFAULT_SETTINGS.notify.limit_orders).toBe(true)
    expect(validateSettings({ ...ok(), notify: { ...ok().notify, limit_orders: false } })).toMatchObject({ ok: true, value: { notify: { limit_orders: false } } })
    expect(validateSettings({ ...ok(), notify: { ...ok().notify, limit_orders: 'no' } }).ok).toBe(false)
    const older: Record<string, unknown> = { ...ok().notify }
    delete older.limit_orders
    expect(validateSettings({ ...ok(), notify: older })).toMatchObject({ ok: true, value: { notify: { limit_orders: true } } })
    expect(parseSettings(JSON.stringify({ notify: { limit_orders: false } })).notify.limit_orders).toBe(false)
    expect(parseSettings(JSON.stringify({ notify: { ntfy_topic: 'x' } })).notify.limit_orders).toBe(true)
  })

  it('parseSettings fills defaults for stored partial JSON and survives garbage', () => {
    expect(parseSettings('{}')).toEqual(DEFAULT_SETTINGS)
    expect(parseSettings('not json')).toEqual(DEFAULT_SETTINGS)
    expect(parseSettings(JSON.stringify({ slippage_bps: 50 })).slippage_bps).toBe(50)
  })

  it('bot cannot run without slippage and vault', () => {
    expect(botBlockers(ok(), { vault_id: null })).toEqual(['vault', 'slippage'])
    expect(botBlockers({ ...ok(), slippage_bps: 'unlimited' }, { vault_id: 'v.near' })).toEqual([])
  })
})
