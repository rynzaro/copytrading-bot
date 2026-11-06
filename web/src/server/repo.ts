// Data access. Every function takes the session owner and touches only that owner's rows.
import 'server-only'
import { randomBytes } from 'node:crypto'
import type { DB } from './db'
import { parseSettings, type Settings } from '@/lib/settings'
import { CURATED } from '@/lib/targets'

export type Me = { owner_id: string; vault_id: string | null; bot_public_key: string | null; bot_on: boolean; settings: Settings }

export function ensureUser(db: DB, owner: string, now = Date.now()) {
  db.prepare('INSERT OR IGNORE INTO users (owner_id, created_at) VALUES (?, ?)').run(owner, now)
}

export function getMe(db: DB, owner: string): Me | null {
  const r = db.prepare('SELECT owner_id, vault_id, bot_public_key, bot_on, settings FROM users WHERE owner_id = ?').get(owner) as
    | { owner_id: string; vault_id: string | null; bot_public_key: string | null; bot_on: number; settings: string }
    | undefined
  if (!r) return null
  return { ...r, bot_on: r.bot_on === 1, settings: parseSettings(r.settings) }
}

export function saveSettings(db: DB, owner: string, s: Settings) {
  db.prepare('UPDATE users SET settings = ? WHERE owner_id = ?').run(JSON.stringify(s), owner)
}

export function setBotOn(db: DB, owner: string, on: boolean) {
  db.prepare('UPDATE users SET bot_on = ? WHERE owner_id = ?').run(on ? 1 : 0, owner)
}

export type TargetRow = { target_id: string; enabled: boolean; curated: boolean; note: string | null }

export function listTargets(db: DB, owner: string): TargetRow[] {
  const rows = db.prepare('SELECT target_id, enabled FROM copy_targets WHERE owner_id = ? ORDER BY target_id').all(owner) as {
    target_id: string
    enabled: number
  }[]
  const byId = new Map(rows.map((r) => [r.target_id, r.enabled === 1]))
  const curated = CURATED.map((c) => ({ target_id: c.id, enabled: byId.get(c.id) ?? false, curated: true, note: c.note }))
  const own = rows
    .filter((r) => !CURATED.some((c) => c.id === r.target_id))
    .map((r) => ({ target_id: r.target_id, enabled: r.enabled === 1, curated: false, note: null }))
  return [...curated, ...own]
}

export const MAX_TARGETS = 50

export function setTarget(db: DB, owner: string, target: string, enabled: boolean): boolean {
  const n = (db.prepare('SELECT count(*) n FROM copy_targets WHERE owner_id = ?').get(owner) as { n: number }).n
  const exists = db.prepare('SELECT 1 FROM copy_targets WHERE owner_id = ? AND target_id = ?').get(owner, target)
  if (!exists && n >= MAX_TARGETS) return false
  db.prepare(
    'INSERT INTO copy_targets (owner_id, target_id, enabled) VALUES (?, ?, ?) ON CONFLICT(owner_id, target_id) DO UPDATE SET enabled = excluded.enabled',
  ).run(owner, target, enabled ? 1 : 0)
  return true
}

export function removeTarget(db: DB, owner: string, target: string) {
  db.prepare('DELETE FROM copy_targets WHERE owner_id = ? AND target_id = ?').run(owner, target)
}

export type Position = {
  token_id: string
  amount_in: string | null
  amount_out: string | null
  tx_hash: string | null
  updated_at: number
  venue: string | null
  pool_id: string | null
  target_id: string
}

export function listPositions(db: DB, owner: string): Position[] {
  return db
    .prepare(
      `SELECT b.token_id, b.amount_in, b.amount_out, b.tx_hash, b.updated_at, s.venue, s.pool_id, s.target_id
       FROM copy_buys b JOIN signals s ON s.id = b.signal_id
       WHERE b.owner_id = ? AND b.status = 'filled' ORDER BY b.updated_at DESC`,
    )
    .all(owner) as Position[]
}

export function getPosition(db: DB, owner: string, token: string): Position | null {
  return (listPositions(db, owner).find((p) => p.token_id === token) ?? null) as Position | null
}

export type ActivityRow = { id: number; kind: string; payload: Record<string, unknown>; created_at: number }

export function listActivity(db: DB, owner: string, limit = 100): ActivityRow[] {
  const rows = db
    .prepare('SELECT id, kind, payload, created_at FROM activity WHERE owner_id = ? ORDER BY id DESC LIMIT ?')
    .all(owner, limit) as { id: number; kind: string; payload: string; created_at: number }[]
  return rows.map((r) => {
    let p: unknown = {}
    try {
      p = JSON.parse(r.payload)
    } catch {}
    return { ...r, payload: p && typeof p === 'object' && !Array.isArray(p) ? (p as Record<string, unknown>) : {} }
  })
}

export const TELEGRAM_CODE_TTL_MS = 15 * 60 * 1000

export function createTelegramLink(db: DB, owner: string, now = Date.now()) {
  db.prepare('DELETE FROM telegram_links WHERE owner_id = ? OR expires_at <= ?').run(owner, now)
  const code = randomBytes(6).toString('hex')
  const expires_at = now + TELEGRAM_CODE_TTL_MS
  db.prepare('INSERT INTO telegram_links (code, owner_id, expires_at) VALUES (?, ?, ?)').run(code, owner, expires_at)
  return { code, expires_at }
}
