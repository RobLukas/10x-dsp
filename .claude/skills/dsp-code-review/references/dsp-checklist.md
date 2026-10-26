# DSP checklist — what to look for in a diff

Contents: A. Non-negotiable rules · B. Request path (FR-001…009) · C. Snapshot lifecycle (FR-010…013, 017) · D. Metrics, health, metadata (FR-014…016) · E. Scorer decorators (FR-026) · F. Hot-path performance · G. Go correctness · H. Scope guard

## A. Non-negotiable rules (Blocker)

| Rule | Look for |
|---|---|
| 1. Identical bidding code | `if variant == ...`, `switch cfg.Variant`, build tags, or env checks inside `pkg/core`; variant-specific fields on core types; core importing a concrete `scorer/redis/logging` package; a second DSP binary or image per variant. Variants must differ only by an interface implementation chosen in `cmd/dsp` wiring from config. `pkg/core` may import only stdlib, `pkg/openrtb`, `pkg/inventory` types. |
| 2. SSP and measurement frozen | Any diff in `pkg/ssp`, `pkg/result`, or HDR recording made to accommodate a DSP variant/scenario. Say: stop and ask. |
| 3. Latency only from SSP HDR | DSP code or docs presenting internal histograms/pprof as result numbers; DSP writing into result files. Internal metrics are diagnostic only (fine to exist). |
| 4. Hard time budget | Budget = `tmax − allowance` computed once at request entry and carried via `context`; every outbound call (Redis, gRPC, anything) gets a deadline derived from the *remaining* budget; `context.Background()`/`context.TODO()` inside a request path; response after budget expiry instead of a no-bid; deadline = fixed constant ignoring the remaining budget (unless it is the deliberate baseline variant). |
| 5. Determinism | `math/rand` global functions or `rand.New(rand.NewSource(time.Now()...))`; `time.Now()` as a seed; map iteration order affecting output (bids, snapshot bytes, tie-breaks); `crypto/rand`; unseeded jitter in clearing price or CTR. Seed must flow from config. |
| 6. Fixed runtime settings | Adding `automaxprocs`; setting GOMAXPROCS/GOGC/GOMEMLIMIT/pool sizes in code differently per variant; settings not exposed through the metadata endpoint. |
| 7. Invalid runs never reported | DSP code that suppresses/clears conditions feeding `cpu_throttled`, `dirty`, `non-reference` flags (e.g. metadata endpoint reporting a clean state it did not check). |
| 8. Prediction lookups local | In `pkg/ctrtable` or the in-process scorer: any network, file or Redis call on lookup; table not built from the run seed (FR-021, FR-025). |

## B. Request path

- FR-001: HTTP/1.1 keep-alive; unknown schema version or malformed body → defined error response + counter, not a panic, not a silent 200. `http.Server` timeouts set; request body size bounded.
- FR-002: every code path (including panics recovered, validation failures, no-match) answers within the budget with bid or no-bid. Watch early returns that skip the response write.
- FR-003: targeting through `Targeter` against the snapshot loaded **once per request** (`atomic.Pointer.Load()` at the top, reuse the same pointer) — loading twice can mix two snapshots. No-match → no-bid with a *recorded reason*.
- FR-004: budget + frequency cap in **one** Redis round-trip via **one** Lua script (atomic). Flag: two commands, `MULTI/EXEC` plus a read, a read-then-write from Go, `EVAL` per campaign in a loop, pipelines, autopipelining enabled.
- FR-005: Redis latency recorded as its own metric; pool stats exported.
- FR-006 (and OD-14): fixed read/write/pool timeouts on the client in addition to context deadlines (context-only cancellation closes connections and churns the pool). On error/timeout → no-bid within budget, reason `state_unavailable`. OD-14 is open: the behaviour must be explicit and ideally configurable, not accidental.
- FR-007/008 (OD-02, OD-10): prediction via `Scorer`; its deadline = min(configured cap, share of remaining budget). Do not demand 15–20 ms; do demand that the cap is configuration and that the deadline is derived from remaining budget.
- FR-009: on scorer failure/timeout/breaker-open → bid with the campaign's `fallback_bid` from the snapshot, tagged as fallback in metrics **and** in response metadata for the SSP. Missing tag in either place is a finding.

## C. Snapshot lifecycle

- FR-011: the pod's **own** subscription is created *before* reading the `latest` manifest (FR-010), otherwise a version published between the two is missed. Check the order in startup code. Subscription is per pod (not shared), 1-day expiration policy. Notifications for version ≤ active are acked and ignored.
- FR-010/015: ready only after snapshot loaded and Redis connectivity established.
- FR-011/012: reload runs in the background; checksum verified **before** the `atomic.Pointer.Store`; on download/checksum/parse failure keep the previous snapshot, count the failure, log it. Flag: swapping then verifying, partially filled structs published before fully built, mutating a snapshot after publish, readers holding locks during reload, ack of the message before a successful load when that loses the update, unbounded concurrent reloads.
- FR-013: pinned version → notifications for other versions ignored (including at startup: load the pinned one, not `latest`).
- FR-017: on graceful shutdown delete the pod's subscription (use a fresh bounded context, the request/server context is already cancelled). Subscription goroutines stop on shutdown.
- Goroutine lifecycle: each background goroutine has an owner, a stop signal and a `WaitGroup`/errgroup; no `time.Sleep` loops without ctx.

## D. Metrics, health, metadata

- FR-014: required series exist and use bounded label cardinality (reasons are an enum; never user ID, campaign ID, request ID as a label).
- FR-015: readiness ≠ liveness; liveness must not depend on Redis/GCS.
- FR-016: all runtime settings come from configuration and are reported by the metadata endpoint, which listens on a **separate port** not exposed via the load balancer. Effective GOMAXPROCS reported.

## E. Scorer decorators (breaker, retry, hedge, deadline)

- FR-026: implemented as `Scorer` decorators in the DSP, not gRPC service config; **every decorator counts the extra calls it sends** (retries, hedged duplicates) — missing counter is a finding.
- Retries/hedges must respect the remaining request budget and cancel losers; hedge goroutines must not leak when the primary wins; no retry storm without budget check.
- Breaker state transitions are race-free and cheap on the hot path (atomics, not a global mutex around the call).
- Decorator order is configuration, not hard-coded in core.

## F. Hot-path performance

Hot path = everything between request parse and response write. Flag:
- allocations per request that can be avoided: `fmt.Sprintf` for keys, `[]byte`↔`string` round trips, `map` creation per request, `interface{}` boxing, `json.Marshal` of large structs, closures capturing per-request state, `defer` in tight loops, `append` without capacity, `slog` calls building args eagerly
- `sync.Mutex` on shared state in the request path (prefer `atomic.Pointer`/sharding); `sync.Pool` misuse (retaining pointers, not resetting)
- blocking calls: synchronous logging in async variants, file/network I/O, unbounded channel sends, `time.Sleep`
- linear scan in `Targeter` allocating a result slice per request
- **No benchmark with the change** → Major. The ask is `go test -bench . -benchmem` on the changed function; before/after if the code existed.
- Do not request micro-optimisations in cold paths (startup, reload, config parsing).

## G. Go correctness

- `ctx` is first parameter and propagated; never stored in structs; `cancel` always called; no `context.Background()` in request scope
- goroutine leaks (no exit path, blocked sends on unbuffered channels after the receiver returned)
- data races: unsynchronised reads of fields written by the reloader; slices/maps shared between snapshot and request; `-race` coverage
- errors wrapped with `%w`, sentinel errors compared with `errors.Is`, no swallowed errors, no panics for expected conditions
- HTTP handler: response body closed, `Content-Type` set, status codes match the defined error responses
- time via an injectable clock where tests need it; tests table-driven, fixed seeds, no `time.Sleep` where a fake clock works

## H. Scope and dependency guard

- New entries in `go.mod` beyond the approved list (go-redis/v9, pubsub, storage, prometheus client, grpc/protobuf, hdrhistogram on the SSP side) → ask first; prefer stdlib. A new major dependency or pattern also needs an ADR (`write-adr`).
- `kelindar/bitmap` targeting is post-MVP even though it is in the stack: MVP is linear scan behind `Targeter`.
- Out of scope, do not implement or "prepare for": real exchanges/full OpenRTB, money/billing, creatives, consent/fraud, real ML, attribution, auth/roles, multi-region, autoscaling, custom dashboards. Flag code that adds them. Never flag their absence.
- Silent resolution of an open decision (OD-01…OD-14) → Major: ask to state the assumption, make it configurable if cheap, propose an ADR.
- Comments and docs in English.
