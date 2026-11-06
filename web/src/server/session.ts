// Session cookies: random token in an httpOnly cookie, only its sha256 in the sessions table.
import 'server-only'
import { createHash, randomBytes } from 'node:crypto'
import type { DB } from './db'

export const SESSION_COOKIE = 'ctb_session'
export const SESSION_TTL_MS = 30 * 24 * 3600 * 1000

export const hashToken = (t: string) => createHash('sha256').update(t).digest('hex')

export function createSession(db: DB, ownerId: string, now = Date.now()) {
  const token = randomBytes(32).toString('base64url')
  const expiresAt = now + SESSION_TTL_MS
  db.prepare('DELETE FROM sessions WHERE expires_at <= ?').run(now)
  db.prepare('INSERT INTO sessions (token_hash, owner_id, expires_at) VALUES (?, ?, ?)').run(hashToken(token), ownerId, expiresAt)
  return { token, expiresAt }
}

export function getSessionOwner(db: DB, token: string | undefined | null, now = Date.now()): string | null {
  if (!token) return null
  const row = db.prepare('SELECT owner_id, expires_at FROM sessions WHERE token_hash = ?').get(hashToken(token)) as
    | { owner_id: string; expires_at: number }
    | undefined
  return row && row.expires_at > now ? row.owner_id : null
}

export function deleteSession(db: DB, token: string | undefined | null) {
  if (token) db.prepare('DELETE FROM sessions WHERE token_hash = ?').run(hashToken(token))
}

export function sessionCookie(token: string, expiresAt: number, o: { secure: boolean }): string {
  const parts = [`${SESSION_COOKIE}=${token}`, 'Path=/', 'HttpOnly', 'SameSite=Lax', `Expires=${new Date(expiresAt).toUTCString()}`]
  if (o.secure) parts.push('Secure')
  return parts.join('; ')
}

export function clearCookie(o: { secure: boolean }): string {
  return sessionCookie('', 0, o)
}

export function readCookie(header: string | null, name = SESSION_COOKIE): string | null {
  if (!header) return null
  for (const part of header.split(';')) {
    const i = part.indexOf('=')
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim() || null
  }
  return null
}
