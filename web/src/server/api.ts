// JSON API. One router so handlers are testable with plain Request objects.
// The owner always comes from the session cookie, never from the request body or URL.
import 'server-only'
import type { DB } from './db'
import { login, LOGIN_MESSAGE } from './auth'
import type { NonceStore } from './nonce'
import type { Rpc } from './rpc'
import type { Worker } from './worker'
import { clearCookie, deleteSession, getSessionOwner, readCookie, sessionCookie } from './session'
import * as repo from './repo'
import { quoteHop } from './quote'
import { botBlockers, validateSettings } from '@/lib/settings'
import { isAccountId, WRAP } from '@/lib/near'
import { setupBlocker, vaultIdFor } from '@/lib/vault-setup'
import { minOut, sellRoute, type SwapArgs } from '@/lib/owner-txs'

export type ApiDeps = {
  db: DB
  nonces: NonceStore
  rpc: Rpc
  worker: Worker
  appHost: string // NEP-413 recipient and allowed Origin host
  secureCookies: boolean
  now?: () => number
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers },
  })
const err = (status: number, error: string) => json({ error }, status)

async function body(req: Request): Promise<Record<string, unknown>> {
  try {
    const b = await req.json()
    return b && typeof b === 'object' && !Array.isArray(b) ? b : {}
  } catch {
    return {}
  }
}

function sameOrigin(req: Request, host: string): boolean {
  const o = req.headers.get('origin')
  if (!o) return false
  try {
    return new URL(o).host === host
  } catch {
    return false
  }
}

export function createApi(d: ApiDeps) {
  const now = () => (d.now ? d.now() : Date.now())

  return async function handle(req: Request): Promise<Response> {
    const path = new URL(req.url).pathname.replace(/^\/api/, '')
    const m = req.method
    if (m !== 'GET' && !sameOrigin(req, d.appHost)) return err(403, 'bad origin')

    // Public routes.
    if (m === 'GET' && path === '/auth/nonce')
      return json({ nonce: d.nonces.issue(now()), message: LOGIN_MESSAGE, recipient: d.appHost })
    if (m === 'POST' && path === '/auth/login') {
      const b = await body(req)
      const r = await login(
        { accountId: b.accountId, publicKey: b.publicKey, signature: b.signature, nonce: b.nonce, callbackUrl: b.callbackUrl },
        { db: d.db, nonces: d.nonces, rpc: d.rpc, recipient: d.appHost, now: now() },
      )
      if (!r.ok) return err(r.status, r.error)
      return json({ owner_id: r.owner }, 200, { 'set-cookie': sessionCookie(r.token, r.expiresAt, { secure: d.secureCookies }) })
    }

    const token = readCookie(req.headers.get('cookie'))
    if (m === 'POST' && path === '/auth/logout') {
      deleteSession(d.db, token)
      return json({ ok: true }, 200, { 'set-cookie': clearCookie({ secure: d.secureCookies }) })
    }

    const owner = getSessionOwner(d.db, token, now())
    if (!owner) return err(401, 'not logged in')
    const me = repo.getMe(d.db, owner)
    if (!me) return err(401, 'not logged in')

    switch (`${m} ${path}`) {
      case 'GET /me':
        return json(me)

      case 'PUT /settings': {
        const r = validateSettings((await body(req)).settings)
        if (!r.ok) return err(400, r.error)
        const stored = me.settings.notify.telegram_chat_id
        // The chat id is written by the worker after /link; the user may only keep or clear it.
        if (r.value.notify.telegram_chat_id !== null && r.value.notify.telegram_chat_id !== stored)
          return err(400, 'telegram chat is linked via /link only')
        repo.saveSettings(d.db, owner, r.value)
        if (me.bot_on && botBlockers(r.value, me).length) repo.setBotOn(d.db, owner, false)
        return json(repo.getMe(d.db, owner))
      }

      case 'POST /bot': {
        const on = (await body(req)).on === true
        if (on) {
          const blockers = botBlockers(me.settings, me)
          if (blockers.length) return json({ error: 'cannot start bot', blockers }, 409)
        }
        repo.setBotOn(d.db, owner, on)
        return json({ bot_on: on })
      }

      case 'GET /targets':
        return json(repo.listTargets(d.db, owner))

      case 'PUT /targets': {
        const b = await body(req)
        if (!isAccountId(b.target_id)) return err(400, 'not a NEAR account')
        if (b.target_id === owner || b.target_id === me.vault_id) return err(400, 'cannot copy yourself')
        if (!repo.setTarget(d.db, owner, b.target_id, b.enabled !== false)) return err(400, 'too many targets')
        return json(repo.listTargets(d.db, owner))
      }

      case 'DELETE /targets': {
        const b = await body(req)
        if (!isAccountId(b.target_id)) return err(400, 'not a NEAR account')
        repo.removeTarget(d.db, owner, b.target_id)
        return json(repo.listTargets(d.db, owner))
      }

      case 'GET /positions': {
        const ps = repo.listPositions(d.db, owner)
        const out = await Promise.all(
          ps.map(async (p) => {
            let balance: string | null = null
            if (me.vault_id)
              try {
                const b = await d.rpc.view<unknown>(p.token_id, 'ft_balance_of', { account_id: me.vault_id })
                balance = typeof b === 'string' && /^\d{1,40}$/.test(b) ? b : null
              } catch {}
            let symbol: string | null = null
            let decimals: number | null = null
            try {
              const md = await d.rpc.view<{ symbol?: unknown; decimals?: unknown }>(p.token_id, 'ft_metadata', {})
              // Token metadata is untrusted: short plain symbols only.
              if (typeof md?.symbol === 'string' && /^[A-Za-z0-9$._-]{1,16}$/.test(md.symbol)) symbol = md.symbol
              if (Number.isInteger(md?.decimals) && (md.decimals as number) >= 0 && (md.decimals as number) <= 32) decimals = md.decimals as number
            } catch {}
            return { ...p, balance, symbol, decimals, can_sell: sellRoute(p, p.token_id) !== null }
          }),
        )
        return json(out)
      }

      case 'GET /vault': {
        if (!me.vault_id) return json({ vault_id: null })
        try {
          const [acct, wnear, state] = await Promise.all([
            d.rpc.viewAccount(me.vault_id),
            d.rpc.view<unknown>(WRAP, 'ft_balance_of', { account_id: me.vault_id }).catch(() => null),
            d.rpc.view<{ paused?: unknown; bot_key?: unknown }>(me.vault_id, 'get_state', {}).catch(() => null),
          ])
          return json({
            vault_id: me.vault_id,
            near: acct?.amount ?? null,
            storage_usage: acct?.storage_usage ?? null,
            wnear: typeof wnear === 'string' && /^\d+$/.test(wnear) ? wnear : null,
            paused: typeof state?.paused === 'boolean' ? state.paused : null,
            bot_key: typeof state?.bot_key === 'string' ? state.bot_key : null,
          })
        } catch {
          return err(503, 'rpc unavailable')
        }
      }

      case 'POST /sell-quote': {
        const b = await body(req)
        if (!me.vault_id) return err(409, 'no vault')
        if (typeof b.token_id !== 'string' || typeof b.amount !== 'string' || !/^[1-9]\d{0,39}$/.test(b.amount))
          return err(400, 'bad request')
        const p = repo.getPosition(d.db, owner, b.token_id)
        const route = p && sellRoute(p, p.token_id)
        if (!route) return err(409, 'no direct wNEAR route: withdraw the token instead')
        if (me.settings.slippage_bps === null) return err(409, 'set slippage first')
        let quote: string
        try {
          quote = await quoteHop(d.rpc, route.venue, route.hop, b.amount)
        } catch {
          return err(503, 'no quote')
        }
        const args: SwapArgs = { venue: route.venue, hops: [route.hop], amount_in: b.amount, min_out: minOut(quote, me.settings.slippage_bps) }
        return json({ quote, args })
      }

      case 'GET /activity':
        return json(repo.listActivity(d.db, owner))

      case 'POST /telegram-link':
        return json(repo.createTelegramLink(d.db, owner, now()))

      case 'POST /setup/bot-key': {
        const blocked = setupBlocker(owner)
        if (blocked) return err(400, blocked)
        try {
          const r = await d.worker.botKey(owner)
          return json({ public_key: r.public_key, vault_id: vaultIdFor(owner) })
        } catch {
          return err(503, 'worker unavailable')
        }
      }

      case 'POST /setup/confirm': {
        const vault = vaultIdFor(owner)
        if (!vault || (await body(req)).vault_id !== vault) return err(400, 'unexpected vault id')
        try {
          const r = await d.worker.vaultConfirmed(owner, vault)
          if (!r.ok) return err(409, 'vault not confirmed on chain yet')
        } catch {
          return err(503, 'worker unavailable')
        }
        return json(repo.getMe(d.db, owner))
      }
    }
    return err(404, 'not found')
  }
}
