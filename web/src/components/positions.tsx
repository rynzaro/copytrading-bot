'use client'
import { useState } from 'react'
import { Button } from './catalyst/button'
import { Dialog, DialogActions, DialogBody, DialogTitle } from './catalyst/dialog'
import { Card, CardTitle, Mono, Notice, errText } from './ui'
import { send } from './txs'
import { api, type PositionView } from '@/lib/client'
import { formatNear, formatUnits } from '@/lib/near'
import { sellTx, withdrawTokenTx, type SwapArgs } from '@/lib/owner-txs'
import type { Me } from '@/server/types'

const amountText = (p: PositionView) =>
  p.balance == null ? '–' : p.decimals == null ? `${p.balance} raw` : formatUnits(p.balance, p.decimals, 4)

export function Positions({ me, positions, onChanged }: { me: Me; positions: PositionView[]; onChanged: () => void }) {
  const [sell, setSell] = useState<PositionView | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function withdraw(p: PositionView) {
    setError(null)
    try {
      await send(me.owner_id, [withdrawTokenTx(me.vault_id!, p.token_id)])
      onChanged()
    } catch (e) {
      setError(errText(e))
    }
  }

  return (
    <Card>
      <CardTitle>Positions</CardTitle>
      {positions.length === 0 ? (
        <p className="text-sm text-dim">No copy buys yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {positions.map((p) => (
            <li key={p.token_id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold">{p.symbol ?? <Mono>{p.token_id}</Mono>}</div>
                <div className="text-xs/5 text-dim">
                  <span className="tabular">{amountText(p)}</span> · paid {p.amount_in ? formatNear(p.amount_in, 2) : '?'} NEAR · via{' '}
                  {p.target_id}
                </div>
              </div>
              <Button outline onClick={() => setSell(p)} disabled={!p.can_sell || !p.balance || p.balance === '0'}>
                Sell
              </Button>
              <Button plain onClick={() => withdraw(p)} disabled={!p.balance || p.balance === '0'}>
                Withdraw
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      {sell && <SellDialog me={me} p={sell} onClose={() => setSell(null)} onDone={onChanged} />}
    </Card>
  )
}

function SellDialog({ me, p, onClose, onDone }: { me: Me; p: PositionView; onClose: () => void; onDone: () => void }) {
  const [q, setQ] = useState<{ quote: string; args: SwapArgs } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function quote() {
    setError(null)
    setBusy(true)
    try {
      setQ(await api.sellQuote(p.token_id, p.balance!))
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  async function go() {
    if (!q) return
    setBusy(true)
    setError(null)
    try {
      await send(me.owner_id, [sellTx(me.vault_id!, q.args)])
      onDone()
      onClose()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={onClose} size="sm">
      <DialogTitle>Sell {p.symbol ?? p.token_id}</DialogTitle>
      <DialogBody className="space-y-3 text-sm/6">
        <p>
          Sell all <span className="tabular">{amountText(p)}</span> for wNEAR in the trading wallet.
        </p>
        {q && (
          <div className="rounded-[10px] bg-field p-3">
            <div className="flex justify-between">
              <span className="text-dim">Expected</span>
              <span className="tabular">{formatNear(q.quote)} NEAR</span>
            </div>
            <div className="flex justify-between">
              <span className="text-dim">Minimum</span>
              <span className="tabular">{q.args.min_out === '1' ? 'any (unlimited slippage)' : `${formatNear(q.args.min_out)} NEAR`}</span>
            </div>
          </div>
        )}
        {error && <Notice tone="bad">{error}</Notice>}
      </DialogBody>
      <DialogActions>
        <Button plain onClick={onClose}>
          Cancel
        </Button>
        {q ? (
          <Button color="amber" onClick={go} disabled={busy}>
            {busy ? 'Signing…' : 'Sign sell'}
          </Button>
        ) : (
          <Button color="amber" onClick={quote} disabled={busy}>
            {busy ? 'Quoting…' : 'Get quote'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
