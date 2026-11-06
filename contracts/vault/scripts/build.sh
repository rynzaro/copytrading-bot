#!/bin/sh
# Build the vault and the sandbox mocks with cargo-near. Uses the rustup toolchain pinned in rust-toolchain.toml.
set -eu
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
cargo near build non-reproducible-wasm --locked
cargo near build non-reproducible-wasm --locked --manifest-path mocks/mock-ft/Cargo.toml
cargo near build non-reproducible-wasm --locked --manifest-path mocks/mock-dex/Cargo.toml
