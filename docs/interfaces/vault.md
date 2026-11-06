# Interface: vault contract

Seam between `contracts/vault` (ticket 0004), `worker` executor (0006) and `web` setup/owner actions (0007). Change only by editing this file first.

## Accounts and keys

- One vault account per owner. Name and creation flow: see ticket 0004 "Account creation" (open).
- The vault account has **no full-access key** after setup. Code changes only through `owner_upgrade`.
- The bot key is a function-call key **on the vault account itself**: receiver = vault account, methods = `["swap", "register_token"]`, allowance = gas allowance (default 1 NEAR).
- Bot calls therefore have `predecessor == current_account`. Owner calls have `predecessor == owner_id`.

## Types (JSON)

```
Venue = "dcl" | "ref_v1"
Hop   = { "pool_id": string, "token_in": AccountId, "token_out": AccountId }   // dcl pool_id "<x>|<y>|<fee>", ref_v1 pool_id numeric string
SwapArgs = {
  "venue": Venue,
  "hops": [Hop],              // 1..3 hops; first token_in = wrap.near for buys; tokens chain hop to hop
  "amount_in": U128,          // in token_in of first hop, yocto
  "min_out": U128,            // > 0, in token_out of last hop
}
```

The vault builds the DEX message itself from `SwapArgs`. Callers never pass raw `msg`.

## Methods

| Method | Caller | Deposit | Effect |
|---|---|---|---|
| `new(owner_id)` | init | – | set owner, DCL + Ref v1 allowlist (constants `dclv2.ref-labs.near`, `v2.ref-finance.near`), unpaused |
| `swap(args: SwapArgs)` | bot | 0 | buy: wraps NEAR if wNEAR balance short (`near_deposit`), then `ft_transfer_call` (1 yocto) to the venue. Rules below. |
| `register_token(token_id)` | bot | 0 | `storage_deposit` on `token_id` for self, max 0.0125 NEAR. Skips if registered. Panics if the storage budget for the current 24 h window would be exceeded. |
| `set_storage_budget(per_day: U128)` | owner | 1 yocto | default 0.1 NEAR per 24 h window |
| `sell(args: SwapArgs)` | owner | 1 yocto | same as swap, any token in; last token_out must be `wrap.near` |
| `withdraw_near(amount: U128?)` | owner | 1 yocto | unwrap if needed, transfer NEAR to owner. `null` = all minus storage |
| `withdraw_token(token_id, amount: U128?)` | owner | 1 yocto | `ft_transfer` to owner |
| `pause()` / `unpause()` | owner | 1 yocto | paused: `swap` and `register_token` panic |
| `set_bot_key(public_key, allowance: U128)` | owner | 1 yocto | delete old bot key if any, add FC key as above |
| `remove_bot_key()` | owner | 1 yocto | delete bot key |
| `owner_upgrade(code_hash: Base58)` | owner | 1 yocto | switch to global contract by hash |
| `get_state()` | view | – | `{ owner_id, paused, bot_key: string?, version, storage_budget: {per_day, spent, window_start} }` |

## Swap rules (enforced on chain)

- Not paused. `min_out > 0`. `hops` length 1..3, tokens chain, no token repeats.
- Buy (`swap` by bot): first `token_in == wrap.near`. Last `token_out` not on the non-meme denylist constant (wNEAR, stables, LSTs; copy list from `worker/copytrade_worker/denylist.json` at build time). Intermediate tokens only `wrap.near` or `token.rhealab.near`.
- `amount_in` ≤ vault wNEAR + NEAR balance minus storage reserve.
- Venue receiver is a constant; no other contract gets `ft_transfer_call`.
- Output tokens must end in the vault's own token balance, never in a DEX internal balance. Verify per venue (Ref v1 instant swap via `ft_transfer_call` sends output back; DCL `Swap` sends output back) and test it in sandbox.

## Events (NEP-297 logs, standard `copytrade_vault`, version `1.0.0`)

`swap {venue, token_in, token_out, amount_in, min_out}`, `swap_result {token_out, amount_out | error}`, `token_registered {token_id, deposit}`, `paused`, `unpaused`, `bot_key_set {public_key, allowance}`, `bot_key_removed`, `withdraw {token_id, amount}`.
