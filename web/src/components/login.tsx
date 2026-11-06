'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Checkbox, CheckboxField } from './catalyst/checkbox'
import { Dialog, DialogActions, DialogBody, DialogDescription, DialogTitle } from './catalyst/dialog'
import { Label } from './catalyst/fieldset'
import { Card, Mono, Notice, errText } from './ui'
import { api } from '@/lib/client'

export function Login({ onLogin }: { onLogin: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [create, setCreate] = useState(false)

  async function go() {
    setBusy(true)
    setError(null)
    try {
      const w = await import('@/lib/wallet')
      await w.connect()
      const signed = await w.signLogin(await api.nonce())
      await api.login(signed)
      onLogin()
    } catch (e) {
      if (errText(e) !== 'cancelled') setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-md px-4">
      <Card className="space-y-5 p-6">
        <div>
          <h1 className="text-2xl font-semibold">Copytrade</h1>
          <p className="mt-1 text-sm/6 text-dim">Copy meme buys of chosen NEAR wallets from a trading wallet only you can withdraw from.</p>
        </div>
        <Button color="amber" className="w-full" onClick={go} disabled={busy}>
          {busy ? 'Waiting for wallet…' : 'Connect wallet'}
        </Button>
        {error && <Notice tone="bad">{error}</Notice>}
        <p className="text-xs/5 text-dim">
          Meteor, MyNearWallet, HOT or Intear. Login signs a message; it sends no transaction.{' '}
          <button className="underline decoration-dim/50 underline-offset-2 hover:text-txt" onClick={() => setCreate(true)}>
            No wallet yet?
          </button>
        </p>
      </Card>
      <CreateWallet open={create} onClose={() => setCreate(false)} />
    </div>
  )
}

function CreateWallet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [seed, setSeed] = useState<{ phrase: string; account: string } | null>(null)
  const [saved, setSaved] = useState(false)

  async function generate() {
    const { generateSeedPhrase } = await import('near-seed-phrase')
    const bs58 = (await import('bs58')).default
    const { Buffer } = await import('buffer')
    const g = generateSeedPhrase()
    const account = Buffer.from(bs58.decode(g.publicKey.replace('ed25519:', ''))).toString('hex')
    setSeed({ phrase: g.seedPhrase, account })
  }

  function close() {
    setSeed(null) // the phrase is never stored; closing forgets it
    setSaved(false)
    onClose()
  }

  return (
    <Dialog open={open} onClose={() => (!seed || saved) && close()}>
      <DialogTitle>Create a wallet</DialogTitle>
      <DialogDescription>
        An external wallet (Meteor, MyNearWallet, HOT, Intear) is safer. A wallet made here is only as safe as this page when it was
        served.
      </DialogDescription>
      <DialogBody className="space-y-4">
        {!seed ? (
          <Button outline onClick={generate}>
            Generate seed phrase
          </Button>
        ) : (
          <>
            <Notice tone="bad">Shown once. Write it down offline. Anyone with these words owns the wallet. We don&apos;t store it.</Notice>
            <ol className="grid grid-cols-3 gap-2 rounded-[10px] bg-field p-3 font-mono text-sm">
              {seed.phrase.split(' ').map((w, i) => (
                <li key={i}>
                  <span className="text-dim">{i + 1}.</span> {w}
                </li>
              ))}
            </ol>
            <div className="text-sm/6">
              Account: <Mono>{seed.account}</Mono>
              <p className="text-dim">
                Fund it with NEAR, import the phrase into an external wallet (for example MyNearWallet → Import), then connect.
              </p>
            </div>
            <CheckboxField>
              <Checkbox checked={saved} onChange={setSaved} color="amber" />
              <Label>I wrote the phrase down</Label>
            </CheckboxField>
          </>
        )}
      </DialogBody>
      <DialogActions>
        <Button plain onClick={close} disabled={!!seed && !saved}>
          {seed ? 'Done' : 'Cancel'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
