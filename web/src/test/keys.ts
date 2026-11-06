// Test-only key helpers. Keys are generated per test run; never real keys.
import { ed25519 } from '@noble/curves/ed25519.js'
import bs58 from 'bs58'
import { nep413Hash } from '@/server/nep413'

export type TestKey = { secret: Uint8Array; publicKey: string }

export function testKey(): TestKey {
  const secret = ed25519.utils.randomSecretKey()
  return { secret, publicKey: 'ed25519:' + bs58.encode(ed25519.getPublicKey(secret)) }
}

export function signNep413(
  k: TestKey,
  p: { message: string; nonce: Uint8Array; recipient: string; callbackUrl?: string },
): string {
  return Buffer.from(ed25519.sign(nep413Hash(p), k.secret)).toString('base64')
}
