# 0004: Vault contract

Dir: `contracts/vault/`. Interface: `docs/interfaces/vault.md` (binding). ADRs 0001, 0002.

## Do
- Rust, near-sdk (current stable), build with cargo-near (reproducible). Pin toolchain in `rust-toolchain.toml` to one nearcore accepts.
- Implement every method and swap rule in the interface. Events as listed.
- Tests: unit tests for rules; near-workspaces sandbox tests with a mock FT (wrap-like) and mock DEX contracts that mimic DCL `Swap` and Ref v1 instant-swap message shapes (receive `ft_transfer_call`, send output back). Prove:
  - bot key can call only `swap`/`register_token`; can't withdraw, sell, pause, change key;
  - buy rules reject: paused, `min_out == 0`, wrong first token, denylisted output, bad intermediate, unknown venue, too many hops, amount above balance;
  - output lands in the vault's token balance;
  - owner can withdraw NEAR and tokens, pause, sell, rotate and remove bot key;
  - no method lets a non-owner move funds out.
- Verify the real DCL and Ref v1 message formats from Beak `tp/snipe/snipe.py` (read-only) and document them in `contracts/vault/README.md`.

## Account creation (decide and document, don't build UI)
Options: sub-account of the owner (`bot.<owner>`; check implicit-account support), or a factory account. Pick one that leaves the host no key or code control; write the flow as a list of actions the owner signs.

## Out
No mainnet or testnet deploy. No transactions. No global contract publish.
