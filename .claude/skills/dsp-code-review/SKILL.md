---
name: dsp-code-review
description: Backend code review for the Go DSP service of 10x-dsp (cmd/dsp, pkg/core, pkg/openrtb, pkg/scorer, pkg/targeter, pkg/state, pkg/logging, pkg/snapshot, pkg/ctrtable). Use whenever the user asks to review, check, audit or "look over" a DSP change, diff, branch, PR or Go file on the bidding path, snapshot loader, Redis/Lua state store, Scorer decorators (breaker, retry, hedge, deadline), or logging handlers — even if they only say "review my change" or "is this OK to merge" and the diff touches those paths. Adds this repo's measurement-validity rules, PRD requirements (FR/OD) and scope guard on top of a generic Go review. Not for SSP, prediction service, tenx CLI, Terraform or Helm changes.
---

# DSP code review

10x-dsp exists to produce trustworthy latency numbers for competing implementations of the bidding path. A DSP change can be perfectly good
Go and still make the experiment worthless (a variant branch inside `core`, an outbound call without a deadline, an unseeded random). So
review in two layers: **measurement validity first**, then Go quality on the hot path. Generic style comments come last and stay brief.

## 1. Establish scope

Work out what is being reviewed (the user's diff, `git diff`, a branch, or named files). Then sort every touched path:

| Class        | Paths                                                                                                                                                                                      | Treatment                                                                                                                               |
|--------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| DSP-owned    | `cmd/dsp`, `pkg/core`, `pkg/openrtb`, `pkg/scorer/...`, `pkg/targeter/...`, `pkg/state/...`, `pkg/logging/...`, `pkg/snapshot`, `pkg/ctrtable`, `pkg/inventory`, `proto/` (as used by DSP) | Review fully                                                                                                                            |
| Frozen       | `pkg/ssp`, `pkg/result`                                                                                                                                                                    | Any change made to support a DSP variant or scenario is a **Blocker** (Rule 2): say "stop and ask" and do not review the change further |
| Out of scope | `cmd/ssp`, `cmd/prediction`, `cmd/tenx`, `terraform/`, `deploy/`, `.github/`                                                                                                               | Do not review. Name them in the "Scope" line so the user knows they were skipped (infra has the `infra-change` skill)                   |

If a file in scope is only partly visible, say what you could not see rather than guessing.

## 2. Review passes

Read `references/dsp-checklist.md` for the detailed "what to look for in a diff" per rule. In order:

1. **Measurement validity** – the 8 non-negotiable rules from `AGENTS.md`. Any violation is a Blocker.
2. **PRD requirements** – FR-001…FR-017 (DSP service) and FR-026 (decorators). Violation is Major unless the checklist says otherwise.
3. **Hot-path performance** – allocations, locks, blocking, missing benchmark.
4. **Go correctness and concurrency** – context, goroutine lifecycle, races, errors.
5. **Scope and dependency guard** – new modules, out-of-MVP features, silently resolved open decisions.

Read the surrounding code, not just the hunks. Most real defects here are about ordering and lifecycle (what happens before/after the
changed lines), which a hunk alone does not show.

## 3. Judgement calls that prevent bad reviews

- **Deliberate anti-pattern variants are legitimate.** The project compares good and bad patterns on purpose (pairs a–e: fixed/missing
  timeout, no breaker, no retry, sync Redis vs local counters, sync logging). A `Scorer`/`StateStore`/logging implementation that is *meant*
  to be the naive side is fine if it is selected only by startup configuration, is named/documented as the baseline, and needs no change in
  `core`. Flag it only if it leaks into the default configuration or forces a `core` change. Do not report "no timeout here" on a variant
  whose purpose is to have none.
- **Open decisions are not rules.** OD-xx values (prediction deadline cap, no-bid on Redis failure, freq-cap key design, CTR→price formula,
  snapshot format) are still open. Do not demand a specific value. Demand that the code states the assumption, makes it configurable where
  cheap, and that an ADR is proposed (`write-adr`). Silently hard-coding a resolution is the finding.
- **Do not invent absences.** Auth, billing, creatives, multi-region, autoscaling and dashboards are out of scope; their absence is never a
  finding. Benchmarks are required only for hot-path changes, not every PR.
- **Cite only what you have read.** Every FR/OD/US/Rule ID in a finding must exist in `.ai/docs/prd.md` or `AGENTS.md`. If unsure of the ID,
  quote the requirement in words instead. `scripts/check_ids.py` verifies the IDs used in this skill.
- **A clean change deserves a clean verdict.** If you find nothing at Blocker/Major level, say so plainly. Padding the report with nits to
  look thorough lowers trust in the real findings.

## 4. Output format

Use exactly this structure, findings ordered Blocker → Major → Minor → Nit:

```
## DSP review: <what was reviewed>
**Scope:** reviewed <paths>; skipped <paths + why>; assumptions <...>
**Verdict:** BLOCK | CHANGES REQUESTED | APPROVE WITH NITS | APPROVE — one sentence

### Blocker
1. `path/file.go:LINE` — <defect in one sentence> (Rule N / FR-xxx)
   Why it matters here: <effect on the measurement or the budget>
   Fix: <concrete change>

### Major / ### Minor / ### Nit
(same shape; omit empty sections)

### Questions / open decisions touched
- OD-xx: <assumption the code makes, and whether it is configurable; propose ADR if not>
```

Severity: **Blocker** = any non-negotiable rule or a frozen-layer change. **Major** = FR violation, missing deadline/timeout, blocking call
or goroutine leak on the critical path, hot-path change with no benchmark, silently resolved OD. **Minor** = correctness risk off the hot
path, weak tests, unclear errors. **Nit** = naming/comment style (match surrounding code; comments in English).

Tag each finding with the rule or requirement it actually breaks, and only that. If a finding is a Blocker because it would change what a
variant measures but no numbered rule is clearly broken by the visible hunk (for example, replacing the single Lua call with several Redis
commands breaks FR-004, and only threatens Rule 1 if it replaces the default), tag the FR and say in the "Why it matters" line that the
Blocker rating is a measurement-validity judgment. In the Verdict, never claim both "no rule violation" and a Blocker; state the basis once.

Always give `file:line`. For a diff, use the line in the new file. Keep each finding to what the author needs to act: defect, why it matters
here, fix.

## 5. Tests and checks

Check that tests are table-driven and deterministic (fixed seeds, fake clock instead of `time.Sleep`). Suggest, do not run unless asked:
`go build ./...`, `go vet ./...`, `go test -race ./...`, `golangci-lint run`, `govulncheck ./...`, and `go test -bench . -benchmem` for
hot-path changes. Use Taskfile targets from `task --list` if they exist; do not invent targets.
