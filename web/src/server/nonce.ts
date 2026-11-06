// Stateless login nonces: 8-byte ms timestamp | 8 random bytes | 16-byte HMAC. Single use is durable: a used
// nonce is stored in login_nonces (db.sql) until it expires, so a web restart can't reopen it for replay.
import 'server-only'
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import type { DB } from './db'

export class NonceStore {
  static TTL_MS = 5 * 60 * 1000
  constructor(
    private secret: Uint8Array,
    private db: DB,
  ) {}

  private mac(head: Buffer) {
    return createHmac('sha256', this.secret).update(head).digest().subarray(0, 16)
  }

  issue(now = Date.now()): string {
    const head = Buffer.alloc(16)
    head.writeBigUInt64BE(BigInt(now), 0)
    randomBytes(8).copy(head, 8)
    return Buffer.concat([head, this.mac(head)]).toString('base64')
  }

  /** Valid, fresh and not used before. Marks it used (durably). */
  consume(nonce: string, now = Date.now()): boolean {
    const raw = Buffer.from(typeof nonce === 'string' ? nonce : '', 'base64')
    if (raw.length !== 32) return false
    const head = raw.subarray(0, 16)
    if (!timingSafeEqual(raw.subarray(16), this.mac(head))) return false
    const ts = Number(head.readBigUInt64BE(0))
    if (ts > now + 30_000 || now - ts > NonceStore.TTL_MS) return false
    this.db.prepare('DELETE FROM login_nonces WHERE expires_at < ?').run(now)
    const r = this.db
      .prepare('INSERT INTO login_nonces (nonce, expires_at) VALUES (?, ?) ON CONFLICT (nonce) DO NOTHING')
      .run(raw.toString('hex'), ts + NonceStore.TTL_MS)
    return r.changes === 1 // 0 = already used: replay
  }
}
