'use client'
import clsx from 'clsx'
import { Card, CardTitle } from './ui'
import { describeActivity } from '@/lib/activity'
import type { ActivityRow } from '@/server/types'

export function ActivityView({ rows }: { rows: ActivityRow[] }) {
  return (
    <Card>
      <CardTitle>Activity</CardTitle>
      {rows.length === 0 ? (
        <p className="text-sm text-dim">Nothing yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((r) => {
            const a = describeActivity(r.kind, r.payload)
            return (
              <li key={r.id} className="flex items-baseline gap-3 py-2.5 text-sm/6">
                <span className="w-28 shrink-0 tabular text-xs text-dim">{new Date(r.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</span>
                <span className={clsx('min-w-0 flex-1 break-words', a.tone === 'good' && 'text-good', a.tone === 'bad' && 'text-bad')}>
                  {a.title}
                  {a.detail && <span className="text-dim"> · {a.detail}</span>}
                </span>
                {a.tx_hash && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a.tx_hash) && (
                  <a className="text-xs text-dim underline underline-offset-2 hover:text-txt" href={`https://nearblocks.io/txns/${a.tx_hash}`} target="_blank" rel="noreferrer noopener">
                    tx
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
