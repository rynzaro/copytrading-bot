// Generates borsh_vector.json with near-api-js@4 (run once: npm i near-api-js@4; node gen_borsh_vector.mjs).
// Test key from a fixed public seed: not a real key, never funded.
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import nearApi from 'near-api-js';
const { KeyPair, transactions, utils } = nearApi;
const seed = createHash('sha256').update('copytrade-bot test vector seed').digest();
const kp0 = KeyPair.fromString('ed25519:' + utils.serialize.base_encode(Buffer.concat([seed, Buffer.alloc(32)])));
const pub = kp0.getPublicKey().data;
const kp = KeyPair.fromString('ed25519:' + utils.serialize.base_encode(Buffer.concat([seed, Buffer.from(pub)])));
const blockHash = createHash('sha256').update('block').digest();
const args = { args: { venue: 'dcl', hops: [{ pool_id: 'meme.near|wrap.near|2000', token_in: 'wrap.near', token_out: 'meme.near' }], amount_in: '10000000000000000000000000', min_out: '1' } };
const actions = [transactions.functionCall('swap', Buffer.from(JSON.stringify(args)), 100000000000000n, 0n)];
const tx = transactions.createTransaction('alice-vault.near', kp.getPublicKey(), 'alice-vault.near', 12345678901n, actions, blockHash);
const [hash, signed] = await transactions.signTransaction(tx, { sign: async (m) => ({ signature: kp.sign(m).signature, publicKey: kp.getPublicKey() }), getPublicKey: async () => kp.getPublicKey() }, 'alice-vault.near', 'mainnet').catch(async () => {
  const msg = transactions.encodeTransaction(tx); const h = createHash('sha256').update(msg).digest();
  const sig = kp.sign(h).signature;
  return [h, new transactions.SignedTransaction({ transaction: tx, signature: new transactions.Signature({ keyType: 0, data: sig }) })];
});
writeFileSync('borsh_vector.json', JSON.stringify({
  generator: 'near-api-js@4',
  seed_hex: seed.toString('hex'),
  public_key: kp.getPublicKey().toString(),
  signer_id: 'alice-vault.near', receiver_id: 'alice-vault.near', nonce: '12345678901',
  block_hash: utils.serialize.base_encode(blockHash),
  method: 'swap', args_json: JSON.stringify(args), gas: '100000000000000', deposit: '0',
  tx_hash: utils.serialize.base_encode(hash),
  signed_tx_base64: Buffer.from(signed.encode()).toString('base64'),
}, null, 2) + '\n');
console.log('ok');
