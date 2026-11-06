'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Input } from './catalyst/input'
import { Switch } from './catalyst/switch'
import { Card, CardTitle, Mono, Notice, errText } from './ui'
import { api } from '@/lib/client'
import { isAccountId } from '@/lib/near'
import type { TargetRow } from '@/server/types'

export function Targets({ targets, setTargets }: { targets: TargetRow[]; setTargets: (t: TargetRow[]) => void }) {
  const [entry, setEntry] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function run(f: () => Promise<TargetRow[]>) {
    setError(null)
    try {
      setTargets(await f())
    } catch (e) {
      setError(errText(e))
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault()
    const id = entry.trim().toLowerCase()
    if (!isAccountId(id)) return setError('Not a NEAR account')
    await run(() => api.setTarget(id, true))
    setEntry('')
  }

  return (
    <Card>
      <CardTitle>Copy</CardTitle>
      <ul className="divide-y divide-line">
        {targets.map((t) => (
          <li key={t.target_id} className="flex items-center gap-3 py-3">
            <div className="min-w-0 flex-1">
              <Mono className="text-sm">{t.target_id}</Mono>
              {t.note && <p className="text-xs/5 text-dim">{t.note}</p>}
            </div>
            {!t.curated && (
              <button
                className="rounded px-1 text-xs text-dim hover:text-txt"
                onClick={() => run(() => api.removeTarget(t.target_id))}
                aria-label={`Remove ${t.target_id}`}
              >
                Remove
              </button>
            )}
            <Switch
              color="amber"
              checked={t.enabled}
              onChange={(on) => run(() => api.setTarget(t.target_id, on))}
              aria-label={`Copy ${t.target_id}`}
            />
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="mt-3 flex gap-2">
        <Input placeholder="any.near" value={entry} onChange={(e) => setEntry(e.target.value)} aria-label="Add a wallet to copy" />
        <Button outline type="submit">
          Add
        </Button>
      </form>
      {error && (
        <div className="mt-3">
          <Notice tone="bad">{error}</Notice>
        </div>
      )}
    </Card>
  )
}
