'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Checkbox, CheckboxField } from './catalyst/checkbox'
import { Description, Field, Label } from './catalyst/fieldset'
import { Input } from './catalyst/input'
import { Switch, SwitchField } from './catalyst/switch'
import { Card, CardTitle, Mono, Notice, errText } from './ui'
import { send } from './txs'
import { api, type VaultInfo } from '@/lib/client'
import { formatNear, nearToYocto } from '@/lib/near'
import { MAX_BANDS, validateSettings, type Settings as S } from '@/lib/settings'
import { pauseTx, removeBotKeyTx, setBotKeyTx, withdrawNearTx } from '@/lib/owner-txs'
import { GAS_ALLOWANCE_YOCTO } from '@/lib/vault-setup'
import type { Me } from '@/server/types'

type Form = {
  ladder: { mcap: string; size: string }[]
  min_mcap: string
  guard: string
  slippage_pct: string
  unlimited: boolean
  ntfy: string
  still_active: boolean
  limit_orders: boolean
}

const toForm = (s: S): Form => ({
  ladder: s.ladder.map((b) => ({ mcap: String(b.max_mcap_usd), size: b.size_near })),
  min_mcap: String(s.min_mcap_usd),
  guard: s.price_guard_x == null ? '' : String(s.price_guard_x),
  slippage_pct: typeof s.slippage_bps === 'number' ? String(s.slippage_bps / 100) : '',
  unlimited: s.slippage_bps === 'unlimited',
  ntfy: s.notify.ntfy_topic ?? '',
  still_active: s.notify.still_active_weekly,
  limit_orders: s.notify.limit_orders,
})

const num = (v: string) => (v.trim() === '' ? NaN : Number(v.replace(/[_,\s]/g, '')))

function fromForm(f: Form, prev: S): unknown {
  return {
    ladder: f.ladder.map((b) => ({ max_mcap_usd: num(b.mcap), size_near: b.size.trim() })),
    min_mcap_usd: f.min_mcap.trim() === '' ? 0 : num(f.min_mcap),
    price_guard_x: f.guard.trim() === '' ? null : num(f.guard),
    slippage_bps: f.unlimited ? 'unlimited' : f.slippage_pct.trim() === '' ? null : Math.round(num(f.slippage_pct) * 100),
    notify: { ntfy_topic: f.ntfy.trim() || null, telegram_chat_id: prev.notify.telegram_chat_id, still_active_weekly: f.still_active, limit_orders: f.limit_orders },
  }
}

export function SettingsView({ me, vault, onSaved }: { me: Me; vault: VaultInfo | null; onSaved: () => void }) {
  const [f, setF] = useState<Form>(() => toForm(me.settings))
  const [msg, setMsg] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const set = (p: Partial<Form>) => setF((x) => ({ ...x, ...p }))

  async function save(e: React.FormEvent) {
    e.preventDefault()
    const r = validateSettings(fromForm(f, me.settings))
    if (!r.ok) return setMsg({ tone: 'bad', text: r.error })
    try {
      await api.saveSettings(r.value)
      setMsg({ tone: 'good', text: 'Saved' })
      onSaved()
    } catch (e) {
      setMsg({ tone: 'bad', text: errText(e) })
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={save} className="space-y-4">
        <Card>
          <CardTitle>Buy size by market cap</CardTitle>
          <div className="space-y-2">
            {f.ladder.map((b, i) => (
              <div key={i} className="flex items-end gap-2">
                <Field className="flex-1">
                  {i === 0 && <Label>Up to mcap (USD)</Label>}
                  <Input inputMode="numeric" value={b.mcap} onChange={(e) => set({ ladder: f.ladder.map((x, j) => (j === i ? { ...x, mcap: e.target.value } : x)) })} />
                </Field>
                <Field className="flex-1">
                  {i === 0 && <Label>Size (NEAR)</Label>}
                  <Input inputMode="decimal" value={b.size} onChange={(e) => set({ ladder: f.ladder.map((x, j) => (j === i ? { ...x, size: e.target.value } : x)) })} />
                </Field>
                <Button plain disabled={f.ladder.length === 1} onClick={() => set({ ladder: f.ladder.filter((_, j) => j !== i) })} aria-label="Remove band">
                  ×
                </Button>
              </div>
            ))}
            <Button plain disabled={f.ladder.length >= MAX_BANDS} onClick={() => set({ ladder: [...f.ladder, { mcap: '', size: '' }] })}>
              Add band
            </Button>
            <p className="text-xs/5 text-dim">Above the top band or with unknown mcap the bot skips.</p>
            <Field>
              <Label>Min mcap (USD)</Label>
              <Input inputMode="numeric" value={f.min_mcap} onChange={(e) => set({ min_mcap: e.target.value })} />
            </Field>
          </div>
        </Card>

        <Card>
          <CardTitle>Price limits</CardTitle>
          <div className="space-y-4">
            <Field>
              <Label>Slippage (%)</Label>
              <Description>Required. The most the bot pays above the pool price.</Description>
              <Input inputMode="decimal" value={f.slippage_pct} disabled={f.unlimited} onChange={(e) => set({ slippage_pct: e.target.value })} />
            </Field>
            <CheckboxField>
              <Checkbox color="amber" checked={f.unlimited} onChange={(v) => set({ unlimited: v })} />
              <Label>Unlimited slippage</Label>
              <Description>The bot buys at any price. Only the price guard below limits it.</Description>
            </CheckboxField>
            <Field>
              <Label>Price guard (× copied fill price)</Label>
              <Description>Recommended. The bot skips a buy priced above this multiple of what the copied wallet paid.</Description>
              <Input inputMode="decimal" value={f.guard} onChange={(e) => set({ guard: e.target.value })} />
            </Field>
          </div>
        </Card>

        <Card>
          <CardTitle>Notifications</CardTitle>
          <div className="space-y-4">
            <Field>
              <Label>ntfy topic</Label>
              <Description>Keep it secret: anyone with the topic can read your alerts.</Description>
              <Input value={f.ntfy} onChange={(e) => set({ ntfy: e.target.value })} autoComplete="off" />
            </Field>
            <TelegramLink linked={!!me.settings.notify.telegram_chat_id} />
            <SwitchField>
              <Label>Weekly still-active reminder</Label>
              <Description>Reminds you that the bot key still works on your trading wallet.</Description>
              <Switch color="amber" checked={f.still_active} onChange={(v) => set({ still_active: v })} />
            </SwitchField>
            <SwitchField>
              <Label>Limit-order alerts</Label>
              <Description>Tells you when a copy target places a limit buy order and when it fills. The bot never buys on these.</Description>
              <Switch color="amber" checked={f.limit_orders} onChange={(v) => set({ limit_orders: v })} />
            </SwitchField>
          </div>
        </Card>

        <div className="flex items-center gap-3">
          <Button color="amber" type="submit">
            Save
          </Button>
          {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        </div>
      </form>
      {me.vault_id && <VaultActions me={me} vault={vault} onDone={onSaved} />}
    </div>
  )
}

function TelegramLink({ linked }: { linked: boolean }) {
  const [code, setCode] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  return (
    <div className="space-y-2 text-sm/6">
      <div className="flex items-center justify-between gap-3">
        <span>Telegram {linked && <span className="text-good">linked</span>}</span>
        <Button outline onClick={() => api.telegramLink().then((r) => setCode(r.code), (e) => setErr(errText(e)))}>
          {linked ? 'Relink' : 'Link'}
        </Button>
      </div>
      {code && (
        <p className="text-dim">
          Add the Copytrade bot as admin to your channel, then post <Mono className="text-txt">/link {code}</Mono> there within 15
          minutes.
        </p>
      )}
      {err && <Notice tone="bad">{err}</Notice>}
    </div>
  )
}

function VaultActions({ me, vault, onDone }: { me: Me; vault: VaultInfo | null; onDone: () => void }) {
  const [amount, setAmount] = useState('')
  const [msg, setMsg] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null)
  const v = me.vault_id!

  async function run(label: string, f: () => Promise<unknown>) {
    setMsg(null)
    try {
      await f()
      setMsg({ tone: 'good', text: `${label}: sent` })
      onDone()
    } catch (e) {
      setMsg({ tone: 'bad', text: errText(e) })
    }
  }

  function withdraw() {
    let y: string | null = null
    if (amount.trim()) {
      try {
        y = nearToYocto(amount.trim())
      } catch {
        return setMsg({ tone: 'bad', text: 'Enter a NEAR amount, or leave empty for all' })
      }
    }
    run('Withdraw', () => send(me.owner_id, [withdrawNearTx(v, y)]))
  }

  return (
    <Card>
      <CardTitle>Trading wallet</CardTitle>
      <div className="space-y-4 text-sm/6">
        <div className="text-dim">
          <Mono className="text-txt">{v}</Mono>
          {vault?.near != null && <> · {formatNear(BigInt(vault.near) + BigInt(vault.wnear ?? '0'))} NEAR</>}
          {vault?.paused && <span className="text-bad"> · paused</span>}
        </div>
        <div className="flex items-end gap-2">
          <Field className="flex-1">
            <Label>Withdraw NEAR to your wallet</Label>
            <Input inputMode="decimal" placeholder="All" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Button outline onClick={withdraw}>
            Withdraw
          </Button>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button outline onClick={() => run(vault?.paused ? 'Resume' : 'Pause', () => send(me.owner_id, [pauseTx(v, !vault?.paused)]))}>
            {vault?.paused ? 'Resume vault' : 'Pause vault'}
          </Button>
          {me.bot_public_key && (
            <Button
              outline
              onClick={() => run('Refill', () => send(me.owner_id, [setBotKeyTx(v, me.bot_public_key!, GAS_ALLOWANCE_YOCTO)]))}
            >
              Refill bot gas (1 NEAR allowance)
            </Button>
          )}
          <Button plain onClick={() => run('Remove bot key', () => send(me.owner_id, [removeBotKeyTx(v)]))}>
            Remove bot key
          </Button>
        </div>
        <p className="text-xs/5 text-dim">
          Pause and key removal are on chain: they stop the bot even if this server misbehaves. The bot switch at the top is only a
          server setting.
        </p>
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      </div>
    </Card>
  )
}
