// Stateless login nonces: 8-byte ms timestamp | 8 random bytes | 16-byte HMAC. db.sql has no nonce table,
// so single use is tracked in memory for the TTL (one web process).
import 'server-only'
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'

export class NonceStore {
  static TTL_MS = 5 * 60 * 1000
  private used = new Map<string, number>()
  constructor(private secret: Uint8Array) {}

  private mac(head: Buffer) {
    return createHmac('sha256', this.secret).update(head).digest().subarray(0, 16)
  }

  issue(now = Date.now()): string {
    const head = Buffer.alloc(16)
    head.writeBigUInt64BE(BigInt(now), 0)
    randomBytes(8).copy(head, 8)
    return Buffer.concat([head, this.mac(head)]).toString('base64')
  }

  /** Valid, fresh and not used before. Marks it used. */
  consume(nonce: string, now = Date.now()): boolean {
    const raw = Buffer.from(typeof nonce === 'string' ? nonce : '', 'base64')
    if (raw.length !== 32) return false
    const head = raw.subarray(0, 16)
    if (!timingSafeEqual(raw.subarray(16), this.mac(head))) return false
    const ts = Number(head.readBigUInt64BE(0))
    if (ts > now + 30_000 || now - ts > NonceStore.TTL_MS) return false
    for (const [k, t] of this.used) if (now - t > NonceStore.TTL_MS) this.used.delete(k)
    const key = raw.toString('hex')
    if (this.used.has(key)) return false
    this.used.set(key, ts)
    return true
  }
}
