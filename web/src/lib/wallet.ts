'use client'
// Wallet Selector v10, modules pinned in package.json (bundled, no CDN): Meteor, MyNearWallet, HOT, Intear.
import { Buffer } from 'buffer'
import type { WalletSelector } from '@near-wallet-selector/core'
import type { WalletSelectorModal } from '@near-wallet-selector/modal-ui'
import type { PlannedAction, PlannedTx } from './tx'

if (typeof window !== 'undefined' && !(globalThis as { Buffer?: unknown }).Buffer)
  (globalThis as { Buffer?: unknown }).Buffer = Buffer

let ready: Promise<{ selector: WalletSelector; modal: WalletSelectorModal }> | null = null

export function getWallet() {
  if (!ready)
    ready = (async () => {
      const [{ setupWalletSelector }, { setupModal }, meteor, mnw, hot, intear] = await Promise.all([
        import('@near-wallet-selector/core'),
        import('@near-wallet-selector/modal-ui'),
        import('@near-wallet-selector/meteor-wallet'),
        import('@near-wallet-selector/my-near-wallet'),
        import('@near-wallet-selector/hot-wallet'),
        import('@near-wallet-selector/intear-wallet'),
      ])
      const selector = await setupWalletSelector({
        network: 'mainnet',
        modules: [meteor.setupMeteorWallet(), mnw.setupMyNearWallet(), hot.setupHotWallet(), intear.setupIntearWallet()],
      })
      const modal = setupModal(selector, { theme: 'dark' })
      return { selector, modal }
    })()
  return ready
}

/** Open the modal and resolve with the first signed-in account. */
export async function connect(): Promise<string> {
  const { selector, modal } = await getWallet()
  const now = selector.store.getState().accounts[0]?.accountId
  if (now) return now
  return new Promise((resolve, reject) => {
    const sub = selector.store.observable.subscribe((s) => {
      const id = s.accounts[0]?.accountId
      if (id) {
        sub.unsubscribe()
        hide.remove()
        resolve(id)
      }
    })
    const hide = modal.on('onHide', ({ hideReason }) => {
      if (hideReason === 'user-triggered') {
        sub.unsubscribe()
        hide.remove()
        reject(new Error('cancelled'))
      }
    })
    modal.show()
  })
}

export type Login = { accountId: string; publicKey: string; signature: string; nonce: string; callbackUrl: string }

export async function signLogin(n: { nonce: string; message: string; recipient: string }): Promise<Login> {
  const { selector } = await getWallet()
  const wallet = await selector.wallet()
  const callbackUrl = window.location.origin + '/'
  const signed = await wallet.signMessage({
    message: n.message,
    recipient: n.recipient,
    nonce: Buffer.from(n.nonce, 'base64'),
    callbackUrl,
  })
  if (!signed) throw new Error('wallet did not return a signature')
  return { accountId: signed.accountId, publicKey: signed.publicKey, signature: signed.signature, nonce: n.nonce, callbackUrl }
}

export async function disconnect() {
  const { selector } = await getWallet()
  if (selector.isSignedIn()) await (await selector.wallet()).signOut().catch(() => {})
}

async function toAction(a: PlannedAction) {
  const { actionCreators } = await import('@near-wallet-selector/core')
  switch (a.kind) {
    case 'create_account':
      return actionCreators.createAccount()
    case 'transfer':
      return actionCreators.transfer(BigInt(a.deposit))
    case 'function_call':
      return actionCreators.functionCall(a.method, a.args, BigInt(a.gas), BigInt(a.deposit))
    case 'use_global_contract': {
      const bs58 = (await import('bs58')).default
      const { GlobalContractIdentifier } = await import('@near-js/transactions')
      return actionCreators.useGlobalContract(new GlobalContractIdentifier({ CodeHash: bs58.decode(a.code_hash) }))
    }
  }
}

export async function signAndSend(txs: PlannedTx[]) {
  const { selector } = await getWallet()
  const wallet = await selector.wallet()
  const transactions = await Promise.all(
    txs.map(async (t) => ({ receiverId: t.receiver_id, actions: await Promise.all(t.actions.map(toAction)) })),
  )
  return wallet.signAndSendTransactions({ transactions })
}
