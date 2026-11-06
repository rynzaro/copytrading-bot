'use client'
import { useSyncExternalStore } from 'react'
import { Button } from './catalyst/button'
import { Dialog, DialogActions, DialogBody, DialogTitle } from './catalyst/dialog'

const KEY = 'ctb.disclaimer.v1'

export const DISCLAIMER = [
  'Copytrade buys meme tokens on NEAR mainnet with real funds. Meme tokens can go to zero within minutes.',
  'The trading wallet contract is new and unaudited. Copying a wallet does not copy its results.',
  'You can lose everything you deposit. Nothing here is financial advice.',
]

const subscribe = (cb: () => void) => {
  window.addEventListener('storage', cb)
  return () => window.removeEventListener('storage', cb)
}
const read = () => {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function DisclaimerGate() {
  const seen = useSyncExternalStore(subscribe, read, () => true)
  const dismiss = () => {
    try {
      localStorage.setItem(KEY, '1')
    } catch {}
    window.dispatchEvent(new StorageEvent('storage'))
  }
  return (
    <Dialog open={!seen} onClose={() => {}}>
      <DialogTitle>Before you start</DialogTitle>
      <DialogBody className="space-y-3 text-sm/6 text-zinc-300">
        {DISCLAIMER.map((t) => (
          <p key={t}>{t}</p>
        ))}
      </DialogBody>
      <DialogActions>
        <Button color="amber" onClick={dismiss}>
          I understand
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export function Footer() {
  return (
    <footer className="mx-auto max-w-3xl px-4 py-8 text-xs/5 text-dim">
      {DISCLAIMER.join(' ')} Open source (MIT, see repository).
    </footer>
  )
}
