import { beforeEach, describe, expect, it } from 'vitest'
import { harness } from '@/test/harness'
import { DEFAULT_SETTINGS } from '@/lib/settings'

let h: ReturnType<typeof harness>
let a: string
let b: string

function seedBob() {
  const now = Date.now()
  h.db.prepare("UPDATE users SET vault_id='vault.bob.near', settings=? WHERE owner_id='bob.near'").run(
    JSON.stringify({ ...DEFAULT_SETTINGS, slippage_bps: 77, notify: { ...DEFAULT_SETTINGS.notify, ntfy_topic: 'bobsecret' } }),
  )
  h.db.prepare("INSERT INTO copy_targets VALUES ('bob.near','whale.near',1)").run()
  h.db
    .prepare("INSERT INTO signals (id,target_id,token_id,kind,venue,pool_id,tx_hash,seen_at,source) VALUES (1,'whale.near','bobtok.near','swap','dcl','bobtok.near|wrap.near|2000','T',?, 'teardata')")
    .run(now)
  h.db
    .prepare("INSERT INTO copy_buys (owner_id,token_id,signal_id,status,amount_in,created_at,updated_at) VALUES ('bob.near','bobtok.near',1,'filled','1',?,?)")
    .run(now, now)
  h.db.prepare("INSERT INTO activity (owner_id,kind,payload,created_at) VALUES ('bob.near','buy_filled',?,?)").run(
    JSON.stringify({ token_id: 'bobtok.near', amount_in: '1', tx_hash: 'T' }),
    now,
  )
  h.db.prepare("INSERT INTO activity (owner_id,kind,payload,created_at) VALUES (NULL,'system',?,?)").run(JSON.stringify({ text: 'sys' }), now)
}

beforeEach(() => {
  h = harness()
  a = h.login('alice.near')
  b = h.login('bob.near')
  seedBob()
})

const get = async (path: string, token?: string) => (await h.req('GET', path, { token })).json()

describe('authz: alice never sees or changes bob', () => {
  it('every private route needs a session', async () => {
    for (const p of ['/me', '/targets', '/positions', '/activity', '/vault'])
      expect((await h.req('GET', p)).status).toBe(401)
    for (const p of ['/bot', '/telegram-link', '/setup/bot-key', '/setup/confirm', '/sell-quote'])
      expect((await h.req('POST', p, { body: {} })).status).toBe(401)
    expect((await h.req('PUT', '/settings', { body: {} })).status).toBe(401)
  })

  it('reads return only own rows', async () => {
    const me = await get('/me', a)
    expect(me.owner_id).toBe('alice.near')
    expect(me.vault_id).toBeNull()
    expect(me.settings.notify.ntfy_topic).toBeNull()
    expect((await get('/targets', a)).map((t: { target_id: string }) => t.target_id)).not.toContain('whale.near')
    expect(await get('/positions', a)).toEqual([])
    expect(await get('/activity', a)).toEqual([])
    expect((await get('/positions', b))[0].token_id).toBe('bobtok.near')
    expect((await get('/activity', b)).map((x: { kind: string }) => x.kind)).toEqual(['buy_filled'])
    expect(JSON.stringify(await get('/me', a))).not.toContain('bobsecret')
  })

  it('owner_id in the body is ignored', async () => {
    await h.req('PUT', '/targets', { token: a, body: { owner_id: 'bob.near', target_id: 'x.near', enabled: false } })
    await h.req('DELETE', '/targets', { token: a, body: { owner_id: 'bob.near', target_id: 'whale.near' } })
    await h.req('PUT', '/settings', { token: a, body: { owner_id: 'bob.near', settings: DEFAULT_SETTINGS } })
    await h.req('POST', '/bot', { token: a, body: { owner_id: 'bob.near', on: true } })
    expect(h.db.prepare("SELECT target_id, enabled FROM copy_targets WHERE owner_id='bob.near'").all()).toEqual([
      { target_id: 'whale.near', enabled: 1 },
    ])
    expect(h.db.prepare("SELECT owner_id FROM copy_targets WHERE target_id='x.near'").all()).toEqual([{ owner_id: 'alice.near' }])
    expect((await get('/me', b)).settings.slippage_bps).toBe(77)
  })

  it("alice can't quote-sell bob's position or confirm bob's vault", async () => {
    expect((await h.req('POST', '/sell-quote', { token: a, body: { token_id: 'bobtok.near', amount: '1' } })).status).toBe(409)
    expect((await h.req('POST', '/setup/confirm', { token: a, body: { vault_id: 'vault.bob.near' } })).status).toBe(400)
    expect(h.worker.calls).toEqual([])
  })

  it("telegram chat id can't be set by the user", async () => {
    const s = { ...DEFAULT_SETTINGS, notify: { ...DEFAULT_SETTINGS.notify, telegram_chat_id: '-100123' } }
    expect((await h.req('PUT', '/settings', { token: a, body: { settings: s } })).status).toBe(400)
  })
})

describe('bot toggle and owner flows', () => {
  it('bot needs vault and slippage; clearing slippage stops it', async () => {
    const r = await h.req('POST', '/bot', { token: a, body: { on: true } })
    expect(r.status).toBe(409)
    expect((await r.json()).blockers).toEqual(['vault', 'slippage'])
    expect((await h.req('POST', '/bot', { token: b, body: { on: true } })).status).toBe(200)
    await h.req('PUT', '/settings', { token: b, body: { settings: DEFAULT_SETTINGS } })
    expect((await get('/me', b)).bot_on).toBe(false)
  })

  it('setup: bot key then confirm the derived vault id', async () => {
    const k = await (await h.req('POST', '/setup/bot-key', { token: a })).json()
    expect(k.vault_id).toBe('vault.alice.near')
    expect((await h.req('POST', '/setup/confirm', { token: a, body: { vault_id: 'vault.alice.near' } })).status).toBe(200)
    expect(h.worker.calls).toEqual(['bot-key alice.near', 'confirm alice.near vault.alice.near'])
  })

  it('sell quote applies slippage to the reverse route', async () => {
    const r = await (await h.req('POST', '/sell-quote', { token: b, body: { token_id: 'bobtok.near', amount: '500' } })).json()
    expect(r.args).toEqual({
      venue: 'dcl',
      hops: [{ pool_id: 'bobtok.near|wrap.near|2000', token_in: 'bobtok.near', token_out: 'wrap.near' }],
      amount_in: '500',
      min_out: '9923',
    })
  })

  it('positions carry the on-chain vault balance', async () => {
    const p = await get('/positions', b)
    expect(p[0]).toMatchObject({ balance: '42', can_sell: true, symbol: null, decimals: 18 })
    expect(h.views.find((v) => v.method === 'ft_balance_of')).toMatchObject({ contract: 'bobtok.near', args: { account_id: 'vault.bob.near' } })
  })
})
