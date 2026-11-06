import { describe, expect, it } from 'vitest'
import { harness, HOST } from '@/test/harness'
import { signNep413, testKey } from '@/test/keys'
import { LOGIN_MESSAGE } from './auth'

async function signIn(h: ReturnType<typeof harness>, k: ReturnType<typeof testKey>, accountId: string, recipient = HOST) {
  const n = (await (await h.req('GET', '/auth/nonce')).json()) as { nonce: string; message: string; recipient: string }
  expect(n.message).toBe(LOGIN_MESSAGE)
  expect(n.recipient).toBe(HOST)
  const nonce = new Uint8Array(Buffer.from(n.nonce, 'base64'))
  const signature = signNep413(k, { message: LOGIN_MESSAGE, nonce, recipient })
  return { nonce: n.nonce, signature, body: { accountId, publicKey: k.publicKey, signature, nonce: n.nonce } }
}

describe('login', () => {
  it('full-access key + valid signature -> httpOnly session cookie, user row', async () => {
    const k = testKey()
    const h = harness({ keys: { [`alice.near/${k.publicKey}`]: 'full' } })
    const { body } = await signIn(h, k, 'alice.near')
    const r = await h.req('POST', '/auth/login', { body })
    expect(r.status).toBe(200)
    const cookie = r.headers.get('set-cookie')!
    expect(cookie).toMatch(/^ctb_session=[\w-]+;.*HttpOnly.*SameSite=Lax/)
    const token = cookie.split(';')[0].split('=')[1]
    expect((await (await h.req('GET', '/me', { token })).json()).owner_id).toBe('alice.near')
    expect(h.db.prepare('select owner_id from users').all()).toEqual([{ owner_id: 'alice.near' }])
  })

  it('rejects function-call keys and keys of other accounts', async () => {
    const k = testKey()
    const h = harness({ keys: { [`alice.near/${k.publicKey}`]: 'function_call' } })
    expect((await h.req('POST', '/auth/login', { body: (await signIn(h, k, 'alice.near')).body })).status).toBe(401)
    expect((await h.req('POST', '/auth/login', { body: (await signIn(h, k, 'bob.near')).body })).status).toBe(401)
    expect(h.db.prepare('select count(*) n from sessions').get()).toEqual({ n: 0 })
  })

  it('rejects replayed nonces, wrong recipient and missing Origin', async () => {
    const k = testKey()
    const h = harness({ keys: { [`alice.near/${k.publicKey}`]: 'full' } })
    const { body } = await signIn(h, k, 'alice.near')
    expect((await h.req('POST', '/auth/login', { body, origin: null })).status).toBe(403)
    expect((await h.req('POST', '/auth/login', { body, origin: 'https://evil.example' })).status).toBe(403)
    expect((await h.req('POST', '/auth/login', { body })).status).toBe(200)
    expect((await h.req('POST', '/auth/login', { body })).status).toBe(401)
    const evil = await signIn(h, k, 'alice.near', 'evil.example')
    expect((await h.req('POST', '/auth/login', { body: evil.body })).status).toBe(401)
  })

  it('logout deletes the session', async () => {
    const h = harness()
    const token = h.login('alice.near')
    expect((await h.req('GET', '/me', { token })).status).toBe(200)
    await h.req('POST', '/auth/logout', { token })
    expect((await h.req('GET', '/me', { token })).status).toBe(401)
  })
})
