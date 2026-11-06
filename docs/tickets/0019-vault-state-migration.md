# 0019: Vault state versioning and migration

Blocks go-live. If a later `owner_upgrade` switches to code whose state layout differs, every method panics on load, including `withdraw_*`: funds would be stuck until the owner switches back.

- Versioned state (e.g. `enum VersionedState { V2(StateV2) }`) or an owner-only `migrate` with `#[init(ignore_state)]` called in the same tx as the upgrade.
- `owner_upgrade` + `migrate` in one owner action; the web builds it.
- Test: unit test that loads a V2 state blob with V3 test code (a test-only V3 struct). Document the upgrade procedure in the README.
- Safety rule in README: a new version must always load every older state.
