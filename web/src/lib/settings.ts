// User settings: shape per docs/interfaces/db.sql (users.settings JSON). Pure, shared by server and client.

export type Band = { max_mcap_usd: number; size_near: string }
export type Slippage = null | number | 'unlimited'
export type Notify = {
  ntfy_topic: string | null
  telegram_chat_id: string | null
  still_active_weekly: boolean
  limit_orders: boolean // alerts on copy targets' limit buy orders (placed, filled); default on
}
export type Settings = {
  ladder: Band[]
  min_mcap_usd: number
  price_guard_x: number | null
  slippage_bps: Slippage
  notify: Notify
}

export const DEFAULT_SETTINGS: Settings = {
  ladder: [
    { max_mcap_usd: 1_000_000, size_near: '10' },
    { max_mcap_usd: 3_000_000, size_near: '5' },
  ],
  min_mcap_usd: 0,
  price_guard_x: null,
  slippage_bps: null,
  notify: { ntfy_topic: null, telegram_chat_id: null, still_active_weekly: true, limit_orders: true },
}

export const MAX_BANDS = 5
const NEAR_AMOUNT = /^(?:0|[1-9]\d{0,8})(?:\.\d{1,24})?$/
const NTFY_TOPIC = /^[A-Za-z0-9_-]{1,64}$/

type Result = { ok: true; value: Settings } | { ok: false; error: string }

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

function onlyKeys(o: Record<string, unknown>, keys: string[]): string | null {
  const extra = Object.keys(o).find((k) => !keys.includes(k))
  return extra ? `unknown field: ${extra}` : null
}

export function validateSettings(input: unknown): Result {
  if (!isObj(input)) return { ok: false, error: 'settings must be an object' }
  const extra = onlyKeys(input, ['ladder', 'min_mcap_usd', 'price_guard_x', 'slippage_bps', 'notify'])
  if (extra) return { ok: false, error: extra }
  const { ladder, min_mcap_usd, price_guard_x, slippage_bps, notify } = input

  if (!Array.isArray(ladder) || ladder.length < 1 || ladder.length > MAX_BANDS)
    return { ok: false, error: `ladder needs 1 to ${MAX_BANDS} bands` }
  let prev = 0
  const bands: Band[] = []
  for (const b of ladder) {
    if (!isObj(b) || onlyKeys(b, ['max_mcap_usd', 'size_near'])) return { ok: false, error: 'bad band' }
    if (!finite(b.max_mcap_usd) || b.max_mcap_usd <= prev) return { ok: false, error: 'band mcaps must rise' }
    if (typeof b.size_near !== 'string' || !NEAR_AMOUNT.test(b.size_near) || Number(b.size_near) <= 0)
      return { ok: false, error: 'band size must be a positive NEAR amount' }
    prev = b.max_mcap_usd
    bands.push({ max_mcap_usd: b.max_mcap_usd, size_near: b.size_near })
  }

  if (!finite(min_mcap_usd) || min_mcap_usd < 0 || min_mcap_usd >= prev)
    return { ok: false, error: 'min mcap must be ≥ 0 and below the top band' }

  if (price_guard_x !== null && (!finite(price_guard_x) || price_guard_x <= 0))
    return { ok: false, error: 'price guard must be empty or a positive multiple' }

  if (
    slippage_bps !== null &&
    slippage_bps !== 'unlimited' &&
    !(Number.isInteger(slippage_bps) && (slippage_bps as number) >= 1 && (slippage_bps as number) <= 10_000)
  )
    return { ok: false, error: 'slippage must be 1 to 10000 bps or unlimited' }

  if (!isObj(notify)) return { ok: false, error: 'notify must be an object' }
  const nx = onlyKeys(notify, ['ntfy_topic', 'telegram_chat_id', 'still_active_weekly', 'limit_orders'])
  if (nx) return { ok: false, error: nx }
  if (notify.ntfy_topic !== null && (typeof notify.ntfy_topic !== 'string' || !NTFY_TOPIC.test(notify.ntfy_topic)))
    return { ok: false, error: 'ntfy topic: letters, digits, _ and -, up to 64' }
  if (notify.telegram_chat_id !== null && typeof notify.telegram_chat_id !== 'string')
    return { ok: false, error: 'bad telegram chat id' }
  if (typeof notify.still_active_weekly !== 'boolean') return { ok: false, error: 'bad still-active flag' }
  if (notify.limit_orders !== undefined && typeof notify.limit_orders !== 'boolean')
    return { ok: false, error: 'bad limit-order alerts flag' }

  return {
    ok: true,
    value: {
      ladder: bands,
      min_mcap_usd,
      price_guard_x: price_guard_x as number | null,
      slippage_bps: slippage_bps as Slippage,
      notify: {
        ntfy_topic: notify.ntfy_topic as string | null,
        telegram_chat_id: notify.telegram_chat_id as string | null,
        still_active_weekly: notify.still_active_weekly,
        limit_orders: notify.limit_orders !== false,
      },
    },
  }
}

/** Read stored JSON, fill missing fields from defaults. Invalid data gives defaults. */
export function parseSettings(json: string): Settings {
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return structuredClone(DEFAULT_SETTINGS)
  }
  if (!isObj(raw)) return structuredClone(DEFAULT_SETTINGS)
  const merged = {
    ...structuredClone(DEFAULT_SETTINGS),
    ...raw,
    notify: { ...DEFAULT_SETTINGS.notify, ...(isObj(raw.notify) ? raw.notify : {}) },
  }
  const r = validateSettings(merged)
  return r.ok ? r.value : structuredClone(DEFAULT_SETTINGS)
}

export type BotBlocker = 'vault' | 'slippage'

export function botBlockers(s: Settings, u: { vault_id: string | null }): BotBlocker[] {
  const out: BotBlocker[] = []
  if (!u.vault_id) out.push('vault')
  if (s.slippage_bps === null) out.push('slippage')
  return out
}
