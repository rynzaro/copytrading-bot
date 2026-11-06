'use client'
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Dialog, DialogActions, DialogBody, DialogTitle } from './catalyst/dialog'
import { Field, Label } from './catalyst/fieldset'
import { Input } from './catalyst/input'
import { Switch } from './catalyst/switch'
import { Notice, errText } from './ui'
import { send } from './txs'
import type { Me } from '@/server/types'
import type { VaultInfo } from '@/lib/client'
import { formatNear, nearToYocto } from '@/lib/near'
import { depositTx } from '@/lib/owner-txs'

export type View = 'main' | 'settings' | 'activity'

export function vaultTotal(v: VaultInfo | null): string | null {
  if (!v?.vault_id || v.near == null) return null
  return formatNear(BigInt(v.near) + BigInt(v.wnear ?? '0'), 2)
}

export function TopBar(p: {
  me: Me
  vault: VaultInfo | null
  view: View
  setView: (v: View) => void
  onBot: (on: boolean) => void
  botError: string | null
  onLogout: () => void
  onChanged: () => void
}) {
  const [deposit, setDeposit] = useState(false)
  const total = vaultTotal(p.vault)
  return (
    <header className="sticky top-0 z-10 border-b border-line bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
        <button onClick={() => p.setView('main')} className="mr-auto text-base font-semibold">
          Copytrade
        </button>
        {p.me.vault_id && (
          <>
            <span className="tabular whitespace-nowrap text-sm" title="Trading wallet balance (NEAR + wNEAR)">
              {total ?? '–'} <span className="text-dim">NEAR</span>
            </span>
            <label className="flex items-center gap-2 text-sm">
              <span className="text-dim">Bot</span>
              <Switch color="amber" checked={p.me.bot_on} onChange={p.onBot} aria-label="Bot on" />
            </label>
            <Button color="amber" onClick={() => setDeposit(true)}>
              Deposit
            </Button>
          </>
        )}
        <Menu>
          <MenuButton aria-label="Menu" className="rounded-lg p-2 text-dim hover:bg-soft hover:text-txt">
            <svg viewBox="0 0 20 20" className="size-5" fill="currentColor" aria-hidden>
              <circle cx="4" cy="10" r="1.6" />
              <circle cx="10" cy="10" r="1.6" />
              <circle cx="16" cy="10" r="1.6" />
            </svg>
          </MenuButton>
          <MenuItems anchor="bottom end" className="z-20 mt-2 min-w-44 rounded-xl border border-line bg-card p-1 text-sm shadow-lg focus:outline-none">
            <div className="truncate px-3 py-2 text-xs text-dim">{p.me.owner_id}</div>
            {(['settings', 'activity'] as const).map((v) => (
              <MenuItem key={v}>
                <button onClick={() => p.setView(v)} className="block w-full rounded-lg px-3 py-2 text-left capitalize data-focus:bg-soft">
                  {v}
                </button>
              </MenuItem>
            ))}
            <MenuItem>
              <button onClick={p.onLogout} className="block w-full rounded-lg px-3 py-2 text-left data-focus:bg-soft">
                Log out
              </button>
            </MenuItem>
          </MenuItems>
        </Menu>
      </div>
      {p.botError && (
        <div className="mx-auto max-w-3xl px-4 pb-3">
          <Notice tone="bad">{p.botError}</Notice>
        </div>
      )}
      {p.me.vault_id && (
        <DepositDialog open={deposit} onClose={() => setDeposit(false)} me={p.me} onDone={p.onChanged} />
      )}
    </header>
  )
}

function DepositDialog({ open, onClose, me, onDone }: { open: boolean; onClose: () => void; me: Me; onDone: () => void }) {
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function go() {
    setError(null)
    let y: string
    try {
      y = nearToYocto(amount.trim())
      if (BigInt(y) <= 0n) throw new Error()
    } catch {
      return setError('Enter a NEAR amount')
    }
    setBusy(true)
    try {
      await send(me.owner_id, [depositTx(me.vault_id!, y)])
      onDone()
      onClose()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onClose={onClose} size="sm">
      <DialogTitle>Deposit NEAR</DialogTitle>
      <DialogBody className="space-y-3">
        <Field>
          <Label>Amount</Label>
          <Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
        </Field>
        <p className="text-xs/5 text-dim">
          To <span className="font-mono">{me.vault_id}</span>. Only your wallet can withdraw it.
        </p>
        {error && <Notice tone="bad">{error}</Notice>}
      </DialogBody>
      <DialogActions>
        <Button plain onClick={onClose}>
          Cancel
        </Button>
        <Button color="amber" onClick={go} disabled={busy}>
          {busy ? 'Signing…' : 'Deposit'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
