import { beforeEach, describe, expect, it } from 'vitest'
import { openDb, type DB } from './db'
import { createSession, deleteSession, getSessionOwner, hashToken, sessionCookie, SESSION_TTL_MS } from './session'
import { NonceStore } from './nonce'

let db: DB
beforeEach(() => {
  db = openDb(':memory:', { requireSchema: true })
})

describe('sessions', () => {
  it('stores only the sha256 of the token and resolves the owner', () => {
    const { token } = createSession(db, 'alice.near', 1000)
    const rows = db.prepare('select * from sessions').all() as { token_hash: string }[]
    expect(rows).toHaveLength(1)
    expect(rows[0].token_hash).toBe(hashToken(token))
    expect(rows[0].token_hash).not.toContain(token)
    expect(getSessionOwner(db, token, 2000)).toBe('alice.near')
  })

  it('expires', () => {
    const { token } = createSession(db, 'alice.near', 0)
    expect(getSessionOwner(db, token, SESSION_TTL_MS - 1)).toBe('alice.near')
    expect(getSessionOwner(db, token, SESSION_TTL_MS)).toBeNull()
  })

  it('unknown, empty or deleted tokens resolve to nobody', () => {
    const { token } = createSession(db, 'alice.near', 0)
    expect(getSessionOwner(db, 'nope', 1)).toBeNull()
    expect(getSessionOwner(db, '', 1)).toBeNull()
    expect(getSessionOwner(db, undefined, 1)).toBeNull()
    deleteSession(db, token)
    expect(getSessionOwner(db, token, 1)).toBeNull()
  })

  it('cookie is httpOnly, SameSite=Lax, Secure in production', () => {
    const c = sessionCookie('tok', 1000, { secure: true })
    expect(c).toMatch(/HttpOnly/)
    expect(c).toMatch(/SameSite=Lax/)
    expect(c).toMatch(/Secure/)
    expect(c).toMatch(/Path=\//)
    expect(sessionCookie('tok', 1000, { secure: false })).not.toMatch(/Secure/)
  })
})

describe('login nonce', () => {
  const secret = new Uint8Array(32).fill(1)

  it('stays single use across a restart (new store, same DB and secret)', () => {
    const fresh = openDb(':memory:', { requireSchema: true })
    const n = new NonceStore(secret, fresh).issue(1000)
    expect(new NonceStore(secret, fresh).consume(n, 2000)).toBe(true)
    expect(new NonceStore(secret, fresh).consume(n, 2000)).toBe(false)
  })

  it('deletes expired used nonces', () => {
    const fresh = openDb(':memory:', { requireSchema: true })
    const s = new NonceStore(secret, fresh)
    expect(s.consume(s.issue(0), 0)).toBe(true)
    const count = () => (fresh.prepare('SELECT count(*) AS c FROM login_nonces').get() as { c: number }).c
    expect(count()).toBe(1)
    expect(s.consume(s.issue(NonceStore.TTL_MS + 1), NonceStore.TTL_MS + 1)).toBe(true)
    expect(count()).toBe(1)
  })

  it('issues 32-byte nonces that verify once', () => {
    const s = new NonceStore(secret, db)
    const n = s.issue(1000)
    expect(Buffer.from(n, 'base64')).toHaveLength(32)
    expect(s.consume(n, 2000)).toBe(true)
    expect(s.consume(n, 2000)).toBe(false)
  })

  it('rejects forged, foreign, expired and future nonces', () => {
    const s = new NonceStore(secret, db)
    const other = new NonceStore(new Uint8Array(32).fill(2), db)
    expect(s.consume(other.issue(1000), 1000)).toBe(false)
    expect(s.consume(Buffer.alloc(32).toString('base64'), 1000)).toBe(false)
    expect(s.consume('short', 1000)).toBe(false)
    const n = s.issue(0)
    expect(s.consume(n, NonceStore.TTL_MS + 1)).toBe(false)
    expect(s.consume(s.issue(100_000), 0)).toBe(false)
  })
})
