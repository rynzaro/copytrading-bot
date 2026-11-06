import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { dbPath, openDb } from './db'

const env = { ...process.env }
afterEach(() => {
  process.env = { ...env }
})

describe('shared DB file (worker-api.md "Shared DB file")', () => {
  it('defaults to $DATA_DIR/copytrade.db, DB_PATH overrides', () => {
    process.env.DATA_DIR = '/srv/data'
    delete process.env.DB_PATH
    expect(dbPath()).toBe('/srv/data/copytrade.db')
    process.env.DB_PATH = '/x/y.db'
    expect(dbPath()).toBe('/x/y.db')
  })
  it('opens in WAL with a busy timeout', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ctdb-'))
    const db = openDb(path.join(dir, 'copytrade.db'))
    expect(db.pragma('journal_mode', { simple: true })).toBe('wal')
    expect(db.pragma('busy_timeout', { simple: true })).toBeGreaterThanOrEqual(5000)
    db.close()
  })
})
