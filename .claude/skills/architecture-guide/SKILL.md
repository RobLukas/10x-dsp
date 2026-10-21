---
name: architecture-guide
description: Architecture rules. Use when designing a feature, deciding where code lives, adding a package or interface, crossing a component boundary (DSP, prediction, SSP, inventory builder, tenx CLI), or reviewing a change for structural problems.
---

# Architecture guide

The purpose of the architecture is **fair measurement**. Every rule below exists so that two
variants differ in exactly one thing. When a design choice is ambiguous, pick the option that
keeps comparisons fair, then the simplest one.

For the component diagram read `references/architecture-diagram.md` (image: `.ai/docs/architecture-diagram-v1-0-0.png`).
For requirement details read `.ai/docs/prd.md`.

## 1. Components and the critical path

| Component | Binary | Critical path | Talks to |
|---|---|---|---|
| DSP service | `cmd/dsp` | yes | Redis (Lua, 1 round-trip), prediction (gRPC), GCS + Pub/Sub (off-path, background) |
| CTR prediction service | `cmd/prediction` | yes (remote variant) | nothing — in-memory seeded table |
| SSP simulator | `cmd/ssp` | traffic source, black-box client | DSP via internal passthrough NLB |
| Inventory builder | `cmd/inventory-builder` | no | GCS (snapshot, then `latest` manifest), Pub/Sub (then publish) |
| `tenx` CLI | `cmd/tenx` | no | Helm, Terraform outputs, control + metadata endpoints, GCS results |

Critical path per bid request:

```
SSP ─HTTP/1.1─▶ DSP: parse → budget(tmax − allowance) → Targeter (snapshot) → StateStore (Redis Lua)
                     → Scorer (gRPC or in-process) → price (CTR-based or fallback_bid) → response
```

Anything not on this line (snapshot reload, Pub/Sub, metrics scraping, logging in async variants)
must never block request handling.

## 3. How to answer an architecture question

1. Find the relevant FR/US/OD in `.ai/docs/prd.md` and cite the ID.
2. If the PRD answers it, follow it. If it is an open OD, propose options with trade-offs expressed in what the experiment can observe, make it configurable if cheap, and suggest an ADR (`write-adr`).
3. Prefer the design that keeps the variable under test isolated over the "production-best" design.
4. When components, hops or boundaries change, update `references/architecture-diagram.md` and bump the diagram version in `.ai/docs/`.
