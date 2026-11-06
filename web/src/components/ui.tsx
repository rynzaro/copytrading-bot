import clsx from 'clsx'
import React from 'react'

export function Card({ className, ...props }: React.ComponentPropsWithoutRef<'section'>) {
  return <section {...props} className={clsx('rounded-(--radius-card) border border-line bg-card p-5 shadow-[0_18px_60px_#0003]', className)} />
}

export function CardTitle({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-base font-semibold text-txt">{children}</h2>
      {action}
    </div>
  )
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'bad' | 'good'; children: React.ReactNode }) {
  return (
    <div
      role={tone === 'bad' ? 'alert' : 'status'}
      className={clsx(
        'rounded-[10px] px-3 py-2 text-sm/6',
        tone === 'bad' && 'bg-bad-bg text-bad',
        tone === 'good' && 'bg-good-bg text-good',
        tone === 'info' && 'bg-accent-soft text-txt',
      )}
    >
      {children}
    </div>
  )
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={clsx('font-mono text-[13px] break-all', className)}>{children}</span>
}

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
