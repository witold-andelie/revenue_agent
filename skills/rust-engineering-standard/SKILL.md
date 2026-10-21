---
name: rust-engineering-standard
description: Build and review Rust services using Rust's official engineering conventions, type-safe APIs, Cargo tooling, and explicit safety boundaries. Use when adding Rust code, broker/execution gateways, low-latency services, FFI, or evaluating whether Python code should be moved to Rust.
---

# Rust Engineering Standard

Use Rust only when measured requirements justify a separate execution service,
such as broker latency, crash isolation, or a native SDK boundary. Keep data
research, Scrapling, yfinance, and model experimentation in Python unless a
benchmark proves otherwise.

## Required workflow

1. Define the process boundary and failure behavior before writing Rust.
2. Create a Cargo workspace with small crates and explicit public interfaces.
3. Run `cargo fmt --check`, `cargo clippy --all-targets --all-features -- -D warnings`, `cargo test`, and `cargo doc --no-deps`.
4. Add deterministic unit tests and at least one integration test for every gateway boundary.
5. Benchmark before and after moving code; do not claim latency improvements without measurements.

## API rules

- Prefer typed enums and structs over stringly-typed commands.
- Make invalid states unrepresentable where practical.
- Use `Result` for recoverable failures and preserve error context.
- Keep `unsafe` out of domain logic; isolate and document every unsafe FFI block.
- Make ownership, cancellation, timeouts, retries, idempotency, and shutdown explicit.
- Keep broker credentials and external tokens outside logs, snapshots, and error messages.

## Trading boundary

- The Rust gateway must expose market-data, order, cancel, fill, position, and health events.
- Server-authoritative prices, freshness checks, margin checks, capability flags, and human strategy adoption are mandatory before live submission.
- Default execution mode is paper-only. Live broker, copy trading, external token use, uncovered shorts, and leverage require explicit configuration and audit records.
- Every request needs an idempotency key; every fill must reconcile against the broker state.

## Review references

- Rust repository: https://github.com/rust-lang/rust
- Rust API Guidelines: https://rust-lang.github.io/api-guidelines/
- Rust contribution workflow: https://github.com/rust-lang/rust/blob/master/CONTRIBUTING.md
