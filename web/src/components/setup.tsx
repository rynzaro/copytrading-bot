'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Field, Label } from './catalyst/fieldset'
import { Input } from './catalyst/input'
import { Card, CardTitle, Mono, Notice, errText } from './ui'
import { send } from './txs'
import { api } from '@/lib/client'
import { formatNear, nearToYocto } from '@/lib/near'
import { BOT_GAS_PER_BUY_NEAR, SETUP_COSTS, buildSetupPlan, setupBlocker, type SetupPlan } from '@/lib/vault-setup'
import type { Me } from '@/server/types'

const CODE_HASH = process.env.NEXT_PUBLIC_VAULT_CODE_HASH ?? ''

export function Setup({ me, onDone }: { me: Me; onDone: () => void }) {
  const [plan, setPlan] = useState<SetupPlan | null>(null)
  const [step, setStep] = useState<'idle' | 'key' | 'sign' | 'confirm'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [funds, setFunds] = useState('')
  const blocked = setupBlocker(me.owner_id)

  async function prepare() {
    setError(null)
    let fundsYocto = '0'
    if (funds.trim()) {
      try {
        fundsYocto = nearToYocto(funds.trim())
      } catch {
        return setError('Enter a NEAR amount for trading funds, or leave it empty')
      }
    }
    setStep('key')
    try {
      const k = await api.botKey()
      const p = buildSetupPlan({ ownerId: me.owner_id, botPublicKey: k.public_key, codeHash: CODE_HASH, fundsYocto })
      if (p.vault_id !== k.vault_id) throw new Error('server and browser disagree on the vault account')
      setPlan(p)
    } catch (e) {
      setError(errText(e))
    } finally {
      setStep('idle')
    }
  }

  async function sign() {
    if (!plan) return
    setError(null)
    try {
      setStep('sign')
      await send(me.owner_id, plan.txs)
      setStep('confirm')
      await confirm(plan.vault_id)
    } catch (e) {
      setError(errText(e))
      setStep('idle')
    }
  }

  async function confirm(vault: string) {
    // The worker checks the chain; finality can lag a few seconds.
    for (let i = 0; i < 6; i++) {
      try {
        await api.confirmVault(vault)
        return onDone()
      } catch (e) {
        if (i === 5) throw e
        await new Promise((r) => setTimeout(r, 3000))
      }
    }
  }

  return (
    <Card>
      <CardTitle>Create your trading wallet</CardTitle>
      <div className="space-y-4 text-sm/6">
        <p className="text-dim">
          The bot trades from a vault account that your wallet owns. Its key can only swap through Rhea DCL and Ref v1. Only you
          can withdraw, sell, pause or change the code.
        </p>
        {blocked && <Notice tone="bad">{blocked}</Notice>}
        {!CODE_HASH && <Notice tone="bad">The vault contract is not published yet. Setup opens at launch.</Notice>}
        {!plan ? (
          <>
            <Field>
              <Label>Trading funds to send with setup (NEAR, optional)</Label>
              <Input inputMode="decimal" placeholder="0" value={funds} onChange={(e) => setFunds(e.target.value)} />
            </Field>
            <Button color="amber" onClick={prepare} disabled={!!blocked || !CODE_HASH || step !== 'idle'}>
              {step === 'key' ? 'Preparing…' : 'Set up'}
            </Button>
          </>
        ) : (
          <>
            <div className="rounded-[10px] bg-field p-3">
              <div className="mb-2 text-dim">
                You sign one transaction to <Mono>{plan.vault_id}</Mono>: create the account, send NEAR, use the vault code,
                set you as owner, add the bot key (swap only, 1 NEAR gas allowance). No full-access key is added.
              </div>
              <ul className="space-y-1">
                <Row label="Storage (locked while the vault exists)" yocto={SETUP_COSTS.account_storage} />
                <Row label="wNEAR registration" yocto={SETUP_COSTS.wrap_storage} />
                <Row label="Bot gas reserve (stays in the vault)" yocto={SETUP_COSTS.gas_reserve} />
              </ul>
              <div className="mt-2 flex justify-between border-t border-line pt-2 font-semibold">
                <span>Setup cost</span>
                <span className="tabular">{formatNear(plan.cost_yocto, 5)} NEAR + gas</span>
              </div>
              {BigInt(plan.funds_yocto) > 0n && (
                <div className="flex justify-between text-dim">
                  <span>Trading funds (yours, withdraw any time)</span>
                  <span className="tabular">{formatNear(plan.funds_yocto)} NEAR</span>
                </div>
              )}
              <div className="mt-2 break-all text-xs/5 text-dim">
                Vault code hash: <Mono>{plan.code_hash}</Mono>. Compare it with the published hash; don&apos;t sign if it differs.
              </div>
            </div>
            <p className="text-xs/5 text-dim">
              Bot gas is paid from the vault, about {BOT_GAS_PER_BUY_NEAR} NEAR per buy, up to the 1 NEAR allowance. Refill it in
              Settings.
            </p>
            <Button color="amber" onClick={sign} disabled={step !== 'idle'}>
              {step === 'sign' ? 'Signing…' : step === 'confirm' ? 'Confirming…' : 'Sign and create'}
            </Button>
          </>
        )}
        {error && <Notice tone="bad">{error}</Notice>}
      </div>
    </Card>
  )
}

function Row({ label, yocto }: { label: string; yocto: string }) {
  return (
    <li className="flex justify-between gap-3">
      <span>{label}</span>
      <span className="tabular text-dim">{formatNear(yocto, 5)} NEAR</span>
    </li>
  )
}
