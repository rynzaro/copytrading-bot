// NEP-413 signed-message verification (server only).
// Payload: borsh(u32 tag = 2^31 + 413) ++ borsh({message, nonce[32], recipient, callbackUrl?}), sha256, ed25519.
import { ed25519 } from '@noble/curves/ed25519.js'
import { sha256 } from '@noble/hashes/sha2.js'
import bs58 from 'bs58'

const TAG = 2 ** 31 + 413

export type Nep413Payload = {
  message: string
  nonce: Uint8Array
  recipient: string
  callbackUrl?: string | null
}

function u32(n: number): Uint8Array {
  const b = new Uint8Array(4)
  new DataView(b.buffer).setUint32(0, n, true)
  return b
}

function str(s: string): Uint8Array {
  const bytes = new TextEncoder().encode(s)
  const out = new Uint8Array(4 + bytes.length)
  out.set(u32(bytes.length), 0)
  out.set(bytes, 4)
  return out
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let o = 0
  for (const p of parts) {
    out.set(p, o)
    o += p.length
  }
  return out
}

export function nep413Hash(p: Nep413Payload): Uint8Array {
  if (p.nonce.length !== 32) throw new Error('nonce must be 32 bytes')
  const cb = p.callbackUrl == null ? new Uint8Array([0]) : concat([new Uint8Array([1]), str(p.callbackUrl)])
  return sha256(concat([u32(TAG), str(p.message), p.nonce, str(p.recipient), cb]))
}

/** Parse "ed25519:<base58>" to 32 raw bytes, or null. */
export function parseEd25519PublicKey(pk: string): Uint8Array | null {
  if (typeof pk !== 'string' || !pk.startsWith('ed25519:')) return null
  try {
    const raw = bs58.decode(pk.slice(8))
    return raw.length === 32 ? raw : null
  } catch {
    return null
  }
}

function decodeSignature(sig: string): Uint8Array | null {
  if (typeof sig !== 'string') return null
  try {
    if (sig.startsWith('ed25519:')) return bs58.decode(sig.slice(8))
    if (!/^[A-Za-z0-9+/_-]+={0,2}$/.test(sig)) return null
    return new Uint8Array(Buffer.from(sig, 'base64'))
  } catch {
    return null
  }
}

export function verifyNep413(
  p: Nep413Payload & { publicKey: string; signature: string },
): boolean {
  const pk = parseEd25519PublicKey(p.publicKey)
  const sig = decodeSignature(p.signature)
  if (!pk || !sig || sig.length !== 64 || p.nonce.length !== 32) return false
  try {
    return ed25519.verify(sig, nep413Hash(p), pk)
  } catch {
    return false
  }
}
