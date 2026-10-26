# AI Rules for 10x-dsp

10x-dsp is a testbed for measuring architectural decisions in high-traffic systems, written in Go. It is not an advertising product.
A minimal but realistic demand-side platform (DSP) serves as a repeatable, well-understood workload. Competing implementations of the
critical bidding path can be swapped in and compared under identical synthetic traffic. Architectural trade-offs and production
anti-patterns are then expressed as numbers instead of opinions.

## Stack

- Go 1.27 (toolchain pinned to `go1.27.x`) for every component, one monorepo
- gRPC + protobuf managed by `buf`; HTTP/1.1 keep-alive JSON (OpenRTB 2.6 subset) between SSP and DSP
- Redis 8.10.x via `go-redis/v9` (single Lua script per request, autopipelining off)
- GCP: GKE Standard (zonal), GCE VM for SSP, GCS, Cloud Pub/Sub, Artifact Registry
- Terraform (persistent + session layers), own Helm charts (no Bitnami)
- Prometheus client, `hdrhistogram-go`, `log/slog`
- Local: Docker Compose with Pub/Sub emulator and fake-gcs-server — results are `non-reference`
- CI: GitHub Actions, golangci-lint, govulncheck; Renovate; task runner: Taskfile

Do not add dependencies outside tech-stack.md without asking. Prefer the standard library.

## Repository layout

```
cmd/            one main package per binary: dsp, prediction, ssp, inventory-builder, tenx
pkg/            shared code (core bidding logic, strategy interfaces and variants, clients)
  core            bidding logic + strategy interfaces. Imports: stdlib + pkg/openrtb + pkg/inventory types only.
  openrtb         versioned JSON subset (id, imp, site/app, device, user, tmax) + validation
  inventory       snapshot model, codec, checksum, deterministic generator (Zipf, seeded)
  scorer/...      Scorer implementations: remote (gRPC), inprocess, decorators (breaker, retry, hedge, deadline)
  targeter/...    Targeter implementations: linear (MVP)
  state/...       StateStore implementations: redis (Lua), local+async (pair d)
  logging/...     slog handlers: sync, async, sampled (pair e)
  ctrtable        seeded CTR table shared by prediction service AND in-process scorer (FR-025)
  snapshot        loader: GCS manifest, Pub/Sub subscription lifecycle, atomic.Pointer swap
  ssp             generator, auction, deadline enforcement, HDR recording, result file   ← frozen for experiments
  result          result file schema + validation                                         ← frozen for experiments
  scenario        scenario schema + validation
  tenx            orchestration, compare, repro, A/A, reports
  gen/...         protobuf code generated from proto/
proto/          buf-managed protobuf contracts (versioned); generated code goes to pkg/gen/...
terraform/      persistent/ and session/ layers
deploy/helm/    own Helm charts for in-cluster services
scenarios/      versioned scenario YAML files
experiments/    experiment reports (hypothesis committed before the first run)
.ai/docs/       PRD, tech stack, ADRs
```

Some of the directories not yet created follow this layout.

## Components and the critical path

| Component              | Binary                  | Critical path                    | Talks to                                                                           |
|------------------------|-------------------------|----------------------------------|------------------------------------------------------------------------------------|
| DSP service            | `cmd/dsp`               | yes                              | Redis (Lua, 1 round-trip), prediction (gRPC), GCS + Pub/Sub (off-path, background) |
| CTR prediction service | `cmd/prediction`        | yes (remote variant)             | nothing — in-memory seeded table                                                   |
| SSP simulator          | `cmd/ssp`               | traffic source, black-box client | DSP via internal passthrough NLB                                                   |
| Inventory builder      | `cmd/inventory-builder` | no                               | GCS (snapshot, then `latest` manifest), Pub/Sub (then publish)                     |
| `tenx` CLI             | `cmd/tenx`              | no                               | Helm, Terraform outputs, control + metadata endpoints, GCS results                 |

Critical path per bid request:

```
SSP ─HTTP/1.1─▶ DSP: parse → budget(tmax − allowance) → Targeter (snapshot) → StateStore (Redis Lua)
                     → Scorer (gRPC or in-process) → price (CTR-based or fallback_bid) → response
```

Anything not on this line (snapshot reload, Pub/Sub, metrics scraping, logging in async variants)
must never block request handling.

## Non-negotiable rules

These protect measurement validity. Breaking any of them makes results worthless, so never trade them for convenience.

1. **Compared variants run identical bidding code.** Bidding logic lives only in the shared `core` package. Variants differ only by
   implementations of strategy interfaces (`Scorer`, `Targeter`, `StateStore`, logging handler), selected by configuration at startup. One
   DSP image for all variants.
2. **The SSP simulator and the measurement layer are frozen for experiments.** Adding a variant or scenario must not require changes there
   (FR-071, US-046). If it seems to, stop and ask.
3. **Reported latency comes only from SSP-side HDR histograms** (FR-034). Internal metrics, pprof and Grafana are diagnostic only.
4. **Every response respects the hard time budget** (`tmax` − network allowance). Every outbound call on the critical path has a deadline
   derived from the remaining budget.
5. **Determinism.** Everything random is seeded: traffic, inventory, CTR table, clearing prices. Same seed → same requests, same snapshot
   bytes, same CTRs.
6. **Runtime settings are fixed across variants** (GOMAXPROCS from CPU limit, GOGC, GOMEMLIMIT, pool sizes) and reported in metadata. Do not
   add `automaxprocs`.
7. **Invalid runs are never reported**: `generator_saturated`, `cpu_throttled`, `aborted`, `non-reference`, `dirty`. Comparisons only within
   one session.
8. **Prediction service lookups make no network calls.** The gRPC hop must be the only thing being measured.

## Working rules for Claude

- Tests: table-driven, deterministic (fixed seeds, no wall-clock sleeps where a fake clock works). Hot-path changes need a benchmark
  (`go test -bench . -benchmem`).
- Keep comments and docs in English.

## Scope guard

Out of scope for the MVP (do not implement, do not "prepare for"): real exchanges and full OpenRTB, money/billing,
creatives, consent/fraud/brand safety, real ML, attribution/warehousing, auth and user roles (only infra-level access control),
multi-region, autoscaling, custom web dashboard, visual polish. Post-MVP backlog is in PRD 4.5 — `kelindar/bitmap` targeting is post-MVP
even though it is in tech-stack; MVP uses linear scan behind `Targeter`.

## Open decisions

PRD section 4.4 lists OD-01 … OD-14. Never silently resolve an open decision in code.
If a task depends on one, state the assumption explicitly, make it configurable where cheap, and propose an ADR (skill `write-adr`).

## Commands

Use Taskfile targets; run `task --list` to see what exists. Do not invent targets — if one is missing, propose adding it.
Typical checks before finishing a Go change:

```
go build ./...
go vet ./...
go test -race ./...
golangci-lint run
govulncheck ./...
```
