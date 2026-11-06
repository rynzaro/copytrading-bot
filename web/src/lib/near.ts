// NEAR helpers shared by server and client. Pure.

const ACCOUNT_ID = /^(?=.{2,64}$)(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$/

export const isAccountId = (s: unknown): s is string => typeof s === 'string' && ACCOUNT_ID.test(s)

export const WRAP = 'wrap.near'
export const RHEA = 'token.rhealab.near'
export const DCL = 'dclv2.ref-labs.near'
export const REF_V1 = 'v2.ref-finance.near'
export const ONE_YOCTO = '1'
export const YOCTO_PER_NEAR = 10n ** 24n

/** "1.5" NEAR -> yocto string. Throws on bad input. */
export function nearToYocto(near: string): string {
  if (!/^\d+(\.\d{1,24})?$/.test(near)) throw new Error('bad NEAR amount')
  const [i, f = ''] = near.split('.')
  return (BigInt(i) * YOCTO_PER_NEAR + BigInt(f.padEnd(24, '0'))).toString()
}

/** Yocto -> NEAR string with up to `dp` decimals, truncated. */
export function formatUnits(raw: string | bigint, decimals: number, dp = 4): string {
  const v = BigInt(raw)
  const base = 10n ** BigInt(decimals)
  const i = v / base
  let f = (v % base).toString().padStart(decimals, '0').slice(0, dp).replace(/0+$/, '')
  if (decimals === 0) f = ''
  return f ? `${i}.${f}` : i.toString()
}

export const formatNear = (yocto: string | bigint, dp = 4) => formatUnits(yocto, 24, dp)
