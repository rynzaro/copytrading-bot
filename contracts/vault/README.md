# Vault contract (trading wallet)

One vault per owner. The server's bot key can only ask the vault to buy a meme token with wNEAR through Rhea DCL or Ref v1. Only the owner moves funds out. Interface: `docs/interfaces/vault.md`. Decisions: ADR 0001, 0002.

Unaudited. Real funds. Read "Residual risks" before funding a vault.

## Why no non-owner path moves funds out

The argument has three parts: who can call what, which actions the contract can emit at all, and where each action sends value.

### 1. Callers

| Caller | How the contract recognises it | Methods |
|---|---|---|
| Owner | `predecessor == owner_id` and exactly 1 yoctoNEAR attached (`assert_owner`) | `sell`, `withdraw_near`, `withdraw_token`, `pause`, `unpause`, `set_bot_key`, `remove_bot_key`, `owner_upgrade` |
| Bot | `predecessor == vault` and `signer_account_pk == stored bot_key` (`assert_bot`), not paused | `swap`, `register_token` |
| Vault itself (callbacks) | `#[private]`: `predecessor == vault` | `on_*` |
| Anyone else | nothing | `get_state` (view) |

Why the bot can't pass the owner check: the bot key is a function-call key **on the vault account**, so its predecessor is the vault, never `owner_id` (`new` refuses `owner_id == vault`).

Why the bot can't reach owner methods or callbacks at all: the protocol enforces the key's permission before the contract runs. The key is `FunctionCall { receiver: vault, methods: [swap, register_token], allowance }`. Any other method, any attached deposit, any receiver other than the vault, and any non-FunctionCall action (Transfer, AddKey, DeleteKey, DeployContract, DeleteAccount, UseGlobalContract) is rejected by the RPC. The sandbox test `bot_key_limited_to_swap_and_register` checks every one of these and the exact rejection kind (`MethodNameMismatch`, `DepositWithFunctionCall`, `ReceiverMismatch`, `RequiresFullAccess`).

Why a function-call key on the owner's account can't act as owner: owner methods need exactly 1 yocto, and function-call keys can't attach a deposit. So owner calls need the owner's full-access key, which the app never holds (ADR 0001).

Why other keys on the vault can't act as bot: there are none. Setup creates the account without AddKey for a full-access key (see "Account creation"); `set_bot_key` is the only key-adding path and adds only the restricted FC key. `assert_bot` also requires the signer key to equal the stored bot key, so a key removed or rotated by the owner loses contract power in the same receipt that clears it from state, before its DeleteKey lands.

Initialisation: `new(owner_id)` requires `predecessor == owner_id`, and near-sdk refuses a second init. Nobody can create a vault that names someone else as owner, and if code were ever deployed without init, a front-runner could only make itself the owner of an account it then has no reason to fund.

### 2. Every action the contract can emit

This is the complete list of outgoing promises in `src/lib.rs`. There is no other `Promise::new`, `ext_*` call, or batch action in the code.

| Action | Receiver | Value | Reached from |
|---|---|---|---|
| `Transfer` | `owner_id` only (`send_near`) | NEAR | `withdraw_near` (owner) → private callbacks |
| `ft_transfer` (1 yocto) | token contract; `receiver_id = owner_id` only (`send_token`) | tokens | `withdraw_token` (owner) |
| `ft_transfer_call` (1 yocto) | `token_in` contract; `receiver_id = venue.receiver()` = `dclv2.ref-labs.near` or `v2.ref-finance.near`, constants | `amount_in` of `token_in` | `swap` (bot, `token_in` = `wrap.near` forced), `sell` (owner) |
| `near_deposit` | `wrap.near` (constant) | NEAR → vault's own wNEAR | `swap` callback, only the shortfall |
| `near_withdraw` (1 yocto) | `wrap.near` | vault's wNEAR → vault's NEAR | `withdraw_near` (owner) |
| `storage_deposit` | `token_id`; `account_id = vault`, `registration_only = true` | ≤ 0.0125 NEAR | `register_token` (bot) |
| `AddKey` FC | vault | – | `set_bot_key` (owner); methods fixed to `swap,register_token`, receiver fixed to vault |
| `DeleteKey` | vault | – | `set_bot_key`, `remove_bot_key` (owner) |
| `UseGlobalContract` (by hash) | vault | – | `owner_upgrade` (owner) |
| views: `ft_balance_of`, `storage_balance_of`, `storage_balance_bounds` | any token | no deposit | swap, register, withdraw |

Not in the code: DeleteAccount, full-access AddKey, DeployContract, Stake, any function call with a caller-chosen method name or receiver, any caller-supplied `msg`.

### 3. Where bot-reachable value goes

The bot reaches four value-moving actions:

- `near_deposit` to `wrap.near`: NEAR becomes the vault's own wNEAR. No value leaves the vault's balance sheet.
- `ft_transfer_call` of wNEAR to a constant venue. The vault builds the message itself from `SwapArgs` (`rules::build_msg`). `SwapArgs` uses `deny_unknown_fields`, so a raw `msg` is refused at parse. Neither message shape has a recipient field (Ref v1's `swap_out_recipient` is never set), so both venues pay the output back to the sender, the vault. If the swap fails, `ft_transfer_call` refunds the input to the vault. Before sending, the vault checks that it is registered on the output token (and on wNEAR), so the venue's output `ft_transfer` can't fail and park tokens in a DEX internal balance or lost-and-found.
- `storage_deposit` of at most 0.0125 NEAR: see residual risk 2.
- Gas: paid from the vault balance, limited by the key allowance (default 1 NEAR). Refunds go back to the allowance (measured, below).

So a non-owner can change **what** the vault holds (wNEAR into meme tokens, within the swap rules), but can't send NEAR or tokens to any account other than the owner, wrap.near and the two venues, and the venues return the output to the vault.

## Swap rules (enforced on chain)

`rules::validate`, plus async checks in `on_swap_checked`:

- `swap` and `register_token` panic when paused; `swap` callback rechecks pause.
- `min_out > 0`, `amount_in > 0`. 1 to 3 hops, hops chain, no token repeats.
- Buy: first token `wrap.near`; last token not on the denylist (`denylist.json`, baked in at build time by `build.rs`: exact contracts plus suffixes `.factory.bridge.near`, `.omft.near`, `.omdep.near`, `.bridge.near`); intermediates only `wrap.near` or `token.rhealab.near`.
- Sell (owner): last token `wrap.near`, same intermediate rule. Works while paused.
- DCL pool id must be `<x>|<y>|<fee>` naming exactly the hop's two tokens; Ref v1 pool id a plain decimal u64.
- `amount_in ≤ vault wNEAR + (NEAR − storage stake − 0.05 NEAR gas reserve)`. Async: the tx succeeds and `swap_result {error: "amount_in above balance"}` is logged, nothing is sent.
- Vault registered on the output token and on wNEAR, else `swap_result {error}`, nothing sent. The worker should call `register_token` first.

`denylist.json` is a copy of `worker/copytrade_worker/denylist.json` (ticket 0005; source copied from the Illia sniper). The unit test `denylist_matches_worker_copy` fails when the two differ, and skips while the worker file doesn't exist. The `config.json allow` override of the worker doesn't exist on chain.

## DEX message formats

Source: Beak `tp/snipe/snipe.py` (read-only), the router-output validators `validate` (buy, lines 147–175) and `validate_sell` (key sets at lines 419–422, checks at 441–475). They accept only these keys, taken from real router output that Beak signs on mainnet:

- `ft_transfer_call` args: `receiver_id, amount, msg, memo`.
- DCL (`dclv2.ref-labs.near`): `msg = {"Swap": {pool_ids, output_token, min_output_amount, skip_unwrap_near, referral_id}}`. Beak: swaps work without DCL storage registration.
- Ref v1 (`v2.ref-finance.near`): `msg = {force, actions, skip_degen_price_sync, referral_id, skip_unwrap_near}`, each action `{pool_id, token_in, token_out, amount_in, amount_out, min_amount_out}`.

What the vault sends (unit tests `dcl_msg_shape`, `ref_msg_shape`; sandbox mock DEX parses with `deny_unknown_fields`):

```json
{"Swap": {"pool_ids": ["meme.near|wrap.near|2000"], "output_token": "meme.near",
          "min_output_amount": "9000000000000000000000000", "skip_unwrap_near": true}}

{"force": 0, "skip_unwrap_near": true, "actions": [
  {"pool_id": 12, "token_in": "wrap.near", "token_out": "token.rhealab.near", "amount_in": "10", "min_amount_out": "0"},
  {"pool_id": 4021, "token_in": "token.rhealab.near", "token_out": "meme.near", "min_amount_out": "7"}]}
```

`amount_in` only on the first Ref action (later hops use the previous output); `min_out` only on the last; `skip_unwrap_near: true` so wNEAR output stays wNEAR. No `referral_id`.

Not yet verified against the live contracts: that DCL `Swap` and Ref instant swap pay output to the sender with a plain `ft_transfer` (both mocks do). Beak's validators rely on this ("the messages allow no receiver field"), but the first mainnet test-wallet run should confirm it from receipts.

## Account creation

Decision: **sub-account of the owner**, e.g. `copytrade.<owner>`, created by one transaction the owner signs. No factory.

Why: only the parent account can create `x.<owner>`, so the host can't create, pre-initialise or squat it. The account never has a full-access key. The code is a global contract referenced **by hash** (immutable), not by publisher account id (which the publisher could re-point). Changing code needs `owner_upgrade`, signed by the owner. A factory would add a contract the host deploys and could change, and would be the predecessor at init.

Flow, one transaction, signer = owner, receiver = `copytrade.<owner>`:

1. `CreateAccount`
2. `Transfer` setup NEAR plus trading funds (setup < 1 NEAR: storage stake of the small state, wNEAR registration; with a normal deploy add ~2.2 NEAR for the 213 KB code)
3. `UseGlobalContract { code_hash }` (mainnet, after the global publish). Test wallets: `DeployContract { code }`.
4. `FunctionCall new { owner_id: <owner> }`, 0 deposit, ~10 Tgas
5. `FunctionCall set_bot_key { public_key: <from worker POST /bot-key>, allowance: "1000000000000000000000000" }`, 1 yocto, 50 Tgas

No `AddKey` action. If any action fails the whole batch reverts. Then the worker: bot `register_token("wrap.near")` (needed before the first buy: `near_deposit` on an unregistered account eats the storage fee from the amount), then `POST /vault-confirmed` checks `get_state()`.

Refill gas allowance: owner calls `set_bot_key` again with the same key (delete + add in one batch).

The sandbox test `setup()` runs exactly this batch (with DeployContract) and `setup_leaves_only_the_bot_key` checks the result: one key, FC, receiver vault, methods `swap`, `register_token`, allowance 1 NEAR.

Implicit owners (64-hex accounts): not supported by this flow. Account ids are at most 64 characters, so `x.<64 hex>` can't exist. Options, none built: require a named owner account; NEP-616 deterministic accounts (near-sdk 5.30 `state_init`, account id derived from code hash + initial state, so no init race; mainnet availability not checked); the `near` linkdrop `create_account_advanced` (has an init race, not recommended). Eth-implicit (`0x…`) owners: same length problem.

## Residual risks

1. **Bad trades by a malicious host.** The bot chooses token, pool and `min_out`. A host can create a meme token and a skewed pool on DCL or Ref v1 (or a skewed wNEAR/RHEA pool for the middle hop) and buy into it with the vault's NEAR. `min_out` protects an honest bot from slippage, not the owner from the bot. Bounded by the vault balance. Accepted in ADR 0002 (mcap and whale origin can't be checked on chain).
2. **Storage-deposit drain.** `register_token` pays up to 0.0125 NEAR to any contract the bot names. A host-made token can claim it's unregistered every time and keep the deposit. Measured: a call burns ~11 Tgas, ~1 milliNEAR of allowance after refunds, so 1 NEAR of allowance allows ~1000 calls: up to ~12 NEAR (bounded by the balance). Smaller than risk 1 in the worst case, but needs no pool and no market. Mitigation not built (interface has no budget field): a registration budget refilled by the owner.
3. **Gas.** Up to the allowance (1 NEAR). Measured in sandbox (gas price 1e8, same as mainnet floor): a Ref v1 buy burns ~27 Tgas; the allowance drops ~0.017 NEAR at signing (200 Tgas prepaid at the pessimistic price) and ~0.0024 NEAR after refunds. A 300 Tgas transaction needs more than 1 NEAR allowance and is refused (`NotEnoughAllowance`), so the worker must send ≤ 200 Tgas.
4. **Untrusted token contracts.** A meme token can lie in `ft_balance_of` / `storage_balance_of`, or refuse transfers. That only affects that token's own balance. `swap_result.amount_out` is the output balance difference and is best effort (a concurrent swap or a late output transfer skews it); the worker should confirm amounts from receipts.
5. **Owner upgrade.** `owner_upgrade` switches to any hash the owner signs. A phishing page could ask for that signature. The web UI must show the expected hash.
6. **Normal-deploy test wallets** lock ~2.2 NEAR of storage stake for the code; the contract has no delete-account path, so it stays locked (mainnet global contract: ~0).

## Build and test

Toolchain: `rust-toolchain.toml` pins Rust 1.95.0 + `wasm32-unknown-unknown` for local builds (cargo-near 0.23 template version). Use rustup's cargo (`~/.cargo/bin` first on PATH); Homebrew cargo ignores the pin. The reproducible build uses the Docker image pinned by digest in `Cargo.toml` (`sourcescan/cargo-near:0.22.0-rust-1.99.1`); that wasm is the one to publish, and its hash changes with every commit (NEP-330 metadata embeds the commit). The full sandbox suite passed against it (212,102 bytes).

```sh
cd contracts/vault
scripts/build.sh                     # vault + mocks, cargo near build non-reproducible-wasm --locked
cargo test --lib                     # unit tests: rules, message shapes, access
cargo test --test sandbox            # near-workspaces sandbox (downloads near-sandbox once)
cargo clippy --all-targets --workspace
cargo near build reproducible-wasm   # Docker, from a clean committed tree; the hash to publish
```

Sandbox setup: mock token (`mocks/mock-ft`, wrap-like) at `wrap.near`, `token.rhealab.near`, `meme.near`, `usdt.tether-token.near`; mock DEX (`mocks/mock-dex`) at `dclv2.ref-labs.near` and `v2.ref-finance.near`, placed by state patch so the vault's constants are tested as built. The mock DEX pays 2 out per 1 in, back to the sender by detached `ft_transfer`, and keeps no user balances. Not tested in sandbox: `owner_upgrade` (near-workspaces 0.24 can't build `UseGlobalContract`; unit-tested for access only).
