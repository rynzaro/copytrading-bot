'use client'
import { useCallback, useEffect, useState } from 'react'
import { ActivityView } from './activity'
import { Login } from './login'
import { Positions } from './positions'
import { SettingsView } from './settings'
import { Setup } from './setup'
import { Targets } from './targets'
import { TopBar, type View } from './topbar'
import { Notice, errText } from './ui'
import { ApiError, api, type PositionView, type VaultInfo } from '@/lib/client'
import type { ActivityRow, Me, TargetRow } from '@/server/types'

const BLOCKER_TEXT = { vault: 'Create the trading wallet first.', slippage: 'Set slippage in Settings first.' } as const

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined)
  const [view, setView] = useState<View>('main')
  const [vault, setVault] = useState<VaultInfo | null>(null)
  const [targets, setTargets] = useState<TargetRow[]>([])
  const [positions, setPositions] = useState<PositionView[]>([])
  const [activity, setActivity] = useState<ActivityRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [botError, setBotError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const m = await api.me()
      setMe(m)
      const [t, p, v] = await Promise.all([api.targets(), api.positions(), m.vault_id ? api.vault().catch(() => null) : null])
      setTargets(t)
      setPositions(p)
      setVault(v)
      setError(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null)
      else setError(errText(e))
    }
  }, [])

  useEffect(() => {
    // Initial fetch of server state; setState happens after the awaited response.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  useEffect(() => {
    if (view === 'activity') api.activity().then(setActivity, (e) => setError(errText(e)))
  }, [view])

  async function onBot(on: boolean) {
    setBotError(null)
    try {
      await api.bot(on)
      await load()
    } catch (e) {
      const b = e instanceof ApiError ? (e.data.blockers as (keyof typeof BLOCKER_TEXT)[] | undefined) : undefined
      setBotError(b?.length ? b.map((x) => BLOCKER_TEXT[x]).join(' ') : errText(e))
    }
  }

  async function logout() {
    await api.logout().catch(() => {})
    await import('@/lib/wallet').then((w) => w.disconnect()).catch(() => {})
    setMe(null)
    setView('main')
  }

  if (me === undefined) return <div className="p-8 text-center text-sm text-dim">{error ?? 'Loading…'}</div>
  if (me === null) return <Login onLogin={load} />

  return (
    <>
      <TopBar me={me} vault={vault} view={view} setView={setView} onBot={onBot} botError={botError} onLogout={logout} onChanged={load} />
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-6">
        {error && <Notice tone="bad">{error}</Notice>}
        {view === 'main' && (
          <>
            {!me.vault_id && <Setup me={me} onDone={load} />}
            <Targets targets={targets} setTargets={setTargets} />
            {me.vault_id && <Positions me={me} positions={positions} onChanged={load} />}
          </>
        )}
        {view === 'settings' && <SettingsView me={me} vault={vault} onSaved={load} />}
        {view === 'activity' && <ActivityView rows={activity} />}
      </main>
    </>
  )
}
