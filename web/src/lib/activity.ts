// Activity rows -> display text. Payload keys per docs/interfaces/db.sql. Payloads are untrusted.
import { formatNear, WRAP } from './near'

export type ActivityView = { title: string; detail: string; tone: 'good' | 'bad' | 'neutral'; tx_hash: string | null }

const s = (v: unknown, max = 80): string | null => (typeof v === 'string' && v.length ? (v.length > max ? v.slice(0, max) + '…' : v) : null)
const near = (v: unknown, dp = 4): string | null => (typeof v === 'string' && /^\d{1,40}$/.test(v) ? `${formatNear(v, dp)} NEAR` : null)
/** NEAR per whole token: 6 significant digits after leading zeros, no exponent. Null unless finite and > 0. */
const price = (v: unknown): string | null => {
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0 || v >= 1e12) return null
  const zeros = v < 1 ? Math.max(0, -Math.floor(Math.log10(v)) - 1) : 0
  const s = v.toFixed(Math.min(20, v < 1 ? zeros + 6 : 4))
  return `${s.includes('.') ? s.replace(/\.?0+$/, '') : s} NEAR`
}
const join = (...parts: (string | null)[]) => parts.filter(Boolean).join(' · ')

export function describeActivity(kind: string, p: Record<string, unknown>): ActivityView {
  const tok = s(p.token_id) ?? '?'
  const tx = s(p.tx_hash, 64)
  const v = (title: string, detail: string, tone: ActivityView['tone'] = 'neutral'): ActivityView => ({ title, detail, tone, tx_hash: tx })
  switch (kind) {
    case 'buy_filled':
      return v(`Bought ${tok}`, join(near(p.amount_in)), 'good')
    case 'buy_failed':
      return v(`Buy failed: ${tok}`, join(s(p.reason), near(p.amount_in)), 'bad')
    case 'buy_skipped':
      return v(`Skipped ${tok}`, join(s(p.reason)))
    case 'storage_paid':
      return v(`Storage paid for ${tok}`, join(near(p.amount, 6)))
    case 'gas_80':
      return v('Gas allowance 80% used', 'Refill the bot key in Settings', 'bad')
    case 'still_active':
      return v('Bot key still active', s(p.vault_id) ?? '')
    case 'sell':
      return v(`Sold ${tok}`, join(near(p.amount_out)), 'good')
    case 'withdraw': {
      const isNear = p.token_id === WRAP || p.token_id === 'near'
      const amt = isNear ? near(p.amount) : typeof p.amount === 'string' && /^\d{1,40}$/.test(p.amount) ? `${p.amount} raw units` : null
      return v(isNear ? 'Withdrew NEAR' : `Withdrew ${tok}`, join(amt))
    }
    case 'limit_order': {
      const who = s(p.target_id) ?? '?'
      const px = price(p.price)
      const title = p.stage === 'filled' ? `Limit buy by ${who} filled: ${tok}` : `${who} placed a limit buy: ${tok}`
      return v(title, join(near(p.amount_near), px && `at ${px}`, 'no copy buy'))
    }
    case 'system':
      return v(s(p.text, 200) ?? 'System', '')
    default:
      return v(s(kind, 40) ?? 'Event', '')
  }
}
