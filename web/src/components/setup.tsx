'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Card, CardTitle, Mono, Notice, errText } from './ui'
import { send } from './txs'
import { api } from '@/lib/client'
import { formatNear } from '@/lib/near'
import { buildSetupPlan, type SetupPlan } from '@/lib/vault-setup'
import type { Me } from '@/server/types'

const CODE_HASH = process.env.NEXT_PUBLIC_VAULT_CODE_HASH ?? ''

export function Setup({ me, onDone }: { me: Me; onDone: () => void }) {
  const [plan, setPlan] = useState<SetupPlan | null>(null)
  const [step, setStep] = useState<'idle' | 'key' | 'sign' | 'confirm'>('idle')
  const [error, setError] = useState<string | null>(null)

  async function prepare() {
    setError(null)
    setStep('key')
    try {
      const k = await api.botKey()
      setPlan(buildSetupPlan({ ownerId: me.owner_id, botPublicKey: k.public_key, codeHash: CODE_HASH }))
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
        {!CODE_HASH && <Notice tone="bad">The vault contract is not published yet. Setup opens at launch.</Notice>}
        {!plan ? (
          <Button color="amber" onClick={prepare} disabled={!CODE_HASH || step !== 'idle'}>
            {step === 'key' ? 'Preparing…' : 'Set up'}
          </Button>
        ) : (
          <>
            <div className="rounded-[10px] bg-field p-3">
              <div className="mb-2 text-dim">
                Vault <Mono>{plan.vault_id}</Mono>. You sign {plan.txs.length} transactions:
              </div>
              <ul className="space-y-1">
                {plan.txs.map((t) => (
                  <li key={t.label} className="flex justify-between gap-3">
                    <span>{t.label}</span>
                    <span className="tabular text-dim">{formatNear(txCost(t), 5)} NEAR</span>
                  </li>
                ))}
              </ul>
              <div className="mt-2 flex justify-between border-t border-line pt-2 font-semibold">
                <span>Setup cost</span>
                <span className="tabular">{formatNear(plan.cost_yocto, 5)} NEAR + gas</span>
              </div>
            </div>
            <p className="text-xs/5 text-dim">Bot gas allowance: 1 NEAR, refill in Settings. Deposit trading funds after setup.</p>
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

function txCost(t: SetupPlan['txs'][number]): bigint {
  return t.actions.reduce((s, a) => s + ('deposit' in a ? BigInt(a.deposit) : 0n), 0n)
}
