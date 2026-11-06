// SQLite access, server only. Schema is the shared interface docs/interfaces/db.sql.
import 'server-only'
import Database from 'better-sqlite3'
import fs from 'node:fs'
import path from 'node:path'

export type DB = Database.Database

function schemaPath(): string | null {
  const candidates = [
    process.env.DB_SCHEMA,
    path.resolve(process.cwd(), 'docs/interfaces/db.sql'),
    path.resolve(process.cwd(), '../docs/interfaces/db.sql'),
  ].filter((p): p is string => !!p)
  return candidates.find((p) => fs.existsSync(/*turbopackIgnore: true*/ p)) ?? null
}

/** Open the DB and apply the schema (CREATE IF NOT EXISTS) if the schema file is found. */
export function openDb(file: string, opts: { requireSchema?: boolean } = {}): DB {
  const db = new Database(/*turbopackIgnore: true*/ file)
  db.pragma('busy_timeout = 5000')
  const sp = schemaPath()
  if (sp) db.exec(fs.readFileSync(/*turbopackIgnore: true*/ sp, 'utf8'))
  else if (opts.requireSchema) throw new Error('db.sql not found')
  return db
}

let shared: DB | null = null

export function getDb(): DB {
  if (!shared) {
    const dir = process.env.DATA_DIR ?? path.resolve(process.cwd(), 'data')
    fs.mkdirSync(/*turbopackIgnore: true*/ dir, { recursive: true })
    shared = openDb(process.env.DB_PATH ?? path.join(dir, 'copytrade.sqlite'))
  }
  return shared
}
