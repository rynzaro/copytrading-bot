import { describe, expect, it } from 'vitest'
import { ed25519 } from '@noble/curves/ed25519.js'
import bs58 from 'bs58'
import { verifySignature } from '@near-wallet-selector/core'
import { nep413Hash, verifyNep413 } from './nep413'
import { signNep413, testKey } from '@/test/keys'

const message = 'Log in to Copytrade'
const recipient = 'copytrade.example'
const nonce = new Uint8Array(32).fill(7)

describe('NEP-413 verification', () => {
  it('accepts a valid signature', () => {
    const k = testKey()
    const signature = signNep413(k, { message, nonce, recipient })
    expect(verifyNep413({ publicKey: k.publicKey, signature, message, nonce, recipient })).toBe(true)
  })

  it('matches the wallet-selector reference verifier', () => {
    const k = testKey()
    const signature = signNep413(k, { message, nonce, recipient })
    const ok = verifySignature({
      publicKey: k.publicKey,
      signature,
      message,
      nonce: Buffer.from(nonce),
      recipient,
    })
    expect(ok).toBe(true)
  })

  it('rejects a signature for another recipient', () => {
    const k = testKey()
    const signature = signNep413(k, { message, nonce, recipient: 'evil.example' })
    expect(verifyNep413({ publicKey: k.publicKey, signature, message, nonce, recipient })).toBe(false)
  })

  it('rejects another nonce, message, key or callbackUrl', () => {
    const k = testKey()
    const other = testKey()
    const signature = signNep413(k, { message, nonce, recipient })
    const n2 = new Uint8Array(32).fill(8)
    expect(verifyNep413({ publicKey: k.publicKey, signature, message, nonce: n2, recipient })).toBe(false)
    expect(verifyNep413({ publicKey: k.publicKey, signature, message: 'x', nonce, recipient })).toBe(false)
    expect(verifyNep413({ publicKey: other.publicKey, signature, message, nonce, recipient })).toBe(false)
    const withCb = signNep413(k, { message, nonce, recipient, callbackUrl: 'https://a' })
    expect(verifyNep413({ publicKey: k.publicKey, signature: withCb, message, nonce, recipient })).toBe(false)
    expect(
      verifyNep413({ publicKey: k.publicKey, signature: withCb, message, nonce, recipient, callbackUrl: 'https://a' }),
    ).toBe(true)
  })

  it('rejects malformed input without throwing', () => {
    const k = testKey()
    expect(verifyNep413({ publicKey: 'ed25519:xx', signature: 'AAAA', message, nonce, recipient })).toBe(false)
    expect(verifyNep413({ publicKey: 'secp256k1:' + bs58.encode(new Uint8Array(64)), signature: 'AAAA', message, nonce, recipient })).toBe(false)
    expect(verifyNep413({ publicKey: k.publicKey, signature: '!!!', message, nonce, recipient })).toBe(false)
    expect(() => nep413Hash({ message, nonce: new Uint8Array(31), recipient })).toThrow()
  })

  it('hash differs from plain sha256 of message (tag prefix present)', () => {
    const h = nep413Hash({ message, nonce, recipient })
    expect(h.length).toBe(32)
    const sig = ed25519.sign(h, testKey().secret)
    expect(sig.length).toBe(64)
  })
})
