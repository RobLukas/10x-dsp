# Product Requirements Document (PRD) - 10x-dsp

Version: 1.0 (MVP)
Date: October 12, 2026
Owner: @Robert
Status: Draft for implementation

## 1. Product Overview

### 1.1 Summary

10x-dsp is a testbed for measuring architectural decisions in high-traffic systems, written in Go. It is not an advertising product. A minimal but realistic demand-side platform (DSP) serves as a repeatable, well-understood workload. Competing implementations of the critical bidding path can be swapped in and compared under identical synthetic traffic. Architectural trade-offs and production anti-patterns are then expressed as numbers instead of opinions.

### 1.2 Central question

What does it actually cost to split the bidding path across a service boundary?

The DSP is deliberately built to depend on a separate prediction service, reached over gRPC, for every bid decision. The price of that dependency becomes directly measurable:

- added latency, especially in the tail (p99)
- failure modes when the dependency is slow, erroring or unavailable
- degradation under increasing load
- business impact (win rate, share of fallback bids)

### 1.3 Audience and purpose

- Primary user: the author, as a solo researcher and engineer.
- Purposes:
  - personal learning about latency-critical system design
  - a portfolio piece built around published experiment reports
  - an evidence base for choosing patterns in future production projects

### 1.4 Components

| Component | On critical path | Role |
|---|---|---|
| DSP service | Yes | Accepts bid requests, applies targeting, enforces budgets and frequency caps in Redis, consults the prediction service through the `Scorer` interface, returns a bid or no-bid within a hard time budget. |
| CTR prediction service | Yes (remote variant) | gRPC service that returns a predicted click-through rate (CTR) from a static, seeded table of random values held in memory. Latency, variance, error rate and overload behaviour are configurable; a control endpoint applies fault phases during a run. |
| SSP simulator | No (traffic source) | Open-loop, seeded load generator on a GCE VM outside the cluster; enforces the deadline, runs the auction, records results in HDR histograms and writes an immutable result file. |
| Inventory builder | No | Builds versioned inventory snapshots, uploads them to GCS, updates a `latest` manifest and announces new versions through Cloud Pub/Sub. |
| Orchestration and reporting CLI | No | Runs a scenario end to end with one command, compares runs and produces reports. Replaces the web dashboard for the MVP. |

Supporting infrastructure: Redis 8 in the cluster (budgets and frequency caps, part of every configuration), Cloud Pub/Sub (snapshot notifications, managed by GCP, off the critical path), GCS (snapshots, the `latest` manifest, result files and Terraform state), Artifact Registry (images), Prometheus and Grafana (live observation, inclusion in MVP pending confirmation).

### 1.5 Technical baseline

- Language: Go 1.27 for every component (DSP, prediction service, SSP simulator, inventory builder, CLI), with the toolchain pinned to `go1.27.x`. One monorepo. One DSP image; the variant is selected by configuration at startup. The Go version never changes within a comparison series.
- Shared `core` package holds the bidding logic. Variants implement the `Scorer` interface and other strategy interfaces (`Targeter`, `StateStore`, logging handler), so compared variants run identical bidding code.
- Targeting: linear scan behind the `Targeter` interface for the MVP. A bitmap index is a post-MVP variant.
- State: Redis 8 (patched 8.10.x, pinned by image digest) through `go-redis/v9` behind the `StateStore` interface.
- SSP-to-DSP protocol: HTTP/1.1 with keep-alive, carrying a minimal versioned JSON subset of OpenRTB 2.6 (`id`, `imp`, `site`/`app`, `device`, `user`, `tmax`).
- DSP-to-prediction protocol: gRPC with a versioned protobuf contract managed by `buf`. The prediction service sits behind a headless Service, and the DSP uses client-side `round_robin` balancing.
- Reference environment: one persistent zonal GKE Standard cluster with dedicated node pools (DSP and prediction service; Redis) and a separate SSP VM. Node pools are scaled to zero between sessions. Fixed machine types, whole-number CPU requests equal to limits, static CPU manager policy, compact placement, Dataplane V2, pinned GKE version, HPA and cluster autoscaling disabled, fixed replica counts.
- Traffic enters through an internal passthrough Network Load Balancer with `externalTrafficPolicy: Local`.
- Deployment: our own Helm charts for all in-cluster services. Terraform in two layers: a persistent layer (network, cluster control plane, buckets, Pub/Sub topic, Artifact Registry, IAM) and a session layer (node pool sizes, SSP VM). Pods reach GCS and Pub/Sub through Workload Identity Federation for GKE.
- Go runtime settings (effective GOMAXPROCS as set by the runtime from the CPU limit, GOGC, GOMEMLIMIT, connection pool sizes) are fixed across variants and recorded in run metadata. `automaxprocs` is not used.

### 1.6 Delivery model

Solo work with no deadline. The MVP is defined as milestones M1 to M4. After M4, work continues from an open experiments backlog. The platform must stay extensible for future experiments.

| Milestone | Scope | Exit condition |
|---|---|---|
| M1 | Reproducible baseline on GKE | Three consecutive runs agree on p99 within 5% |
| M2 | Cost of the service split | Remote vs in-process measured at 3 or more load levels |
| M3 | Degradation scenarios | Slow, partial failures and unavailable each produce a documented outcome |
| M4 | First five pattern pairs | Pairs a to e each reduced to one actionable number in a published report |

## 2. User Problem

### 2.1 Problem statement

Architectural decisions in high-traffic systems are usually made on intuition, conference talks and other people's benchmarks. Those benchmarks rarely transfer to one's own load profile, hardware and failure modes.

Real-time bidding is an extreme case:

- A DSP has roughly 100 ms to answer a bid request, network time included.
- It handles tens of thousands of requests per second.
- Every design choice shows up immediately in the tail of the latency distribution and in the number of auctions lost to timeouts.

### 2.2 Consequences today

- Debates about service boundaries, timeouts, retries, circuit breakers, state access and logging are settled by argument in code review rather than by measurement.
- The cost of anti-patterns seen in production work is known anecdotally but never quantified on a controlled workload.
- Latency figures are often quoted at comfortable load, which says little about the maximum rate a configuration can sustain under a service level objective.
- Behaviour under dependency failure is assumed rather than observed.

### 2.3 What the user needs

- A realistic, repeatable workload whose results are stable enough to compare (p99 within 5% across runs).
- The ability to swap one implementation of the critical path for another while everything else stays identical.
- Controlled fault injection on the dependency, with measured recovery.
- Results reduced to a single number a reader can act on, published together with the hypothesis made before the run.

## 3. Functional Requirements

Requirement identifiers use the prefix FR. Each requirement is traced to one or more user stories in section 5.

### 3.1 DSP service

- FR-001: Accept bid requests over HTTP/1.1 with keep-alive, in the versioned JSON subset of OpenRTB 2.6. Requests with an unknown schema version or malformed body are rejected with a defined error response and counted in metrics.
- FR-002: Compute a hard time budget per request from `tmax` (minus the configured network allowance) and always respond within it with either a bid or a no-bid.
- FR-003: Apply targeting against the active in-memory inventory snapshot through the `Targeter` interface (linear scan in the MVP). Return a no-bid with a recorded reason when no campaign matches.
- FR-004: Enforce campaign budgets and frequency caps in Redis, combining all Redis operations for one request into a single round-trip through one Lua script, so the checks and updates are atomic.
- FR-005: Record Redis latency as a separate metric, and export Redis client pool statistics.
- FR-006: Default behaviour on Redis error or timeout: respond with a no-bid within the time budget, with reason `state_unavailable` recorded in metrics (proposed default, see 4.4). The Redis client uses fixed read, write and pool timeouts rather than relying only on context cancellation, which closes connections and churns the pool under a slow Redis. Client autopipelining is disabled.
- FR-007: Obtain a predicted CTR through the `Scorer` interface. Variants include at minimum a remote gRPC scorer and an in-process scorer, selected by configuration at startup. The bid price is derived from the CTR (formula open, for example CTR × value per click, see OD-10).
- FR-008: Bound the prediction call with a deadline derived from the remaining time budget, capped by a configured maximum (about 15 to 20 ms, final value open).
- FR-009: When the prediction call fails, times out or is rejected by a resilience mechanism, bid using the campaign's fixed `fallback_bid` from the snapshot and tag the bid as a fallback in metrics and in the response metadata used by the SSP.
- FR-010: At startup, after creating the pod's subscription (FR-011), read the `latest` manifest from GCS and load the referenced snapshot before reporting ready.
- FR-011: At startup, create a dedicated Cloud Pub/Sub subscription for this pod on the snapshot topic, so that every pod receives every notification (a shared subscription would deliver each message to only one pod).
  - The subscription uses a 1-day expiration policy, the minimum Pub/Sub allows.
  - The subscription is created before the pod reads the `latest` manifest (FR-010), so a version published between the two steps is not missed.
  - Notifications for a version at or below the active one are acknowledged and ignored.
  - New snapshots are loaded in the background, checksum-verified and swapped atomically (`atomic.Pointer`) without blocking request handling.
- FR-012: If a snapshot fails to download, fails checksum verification or fails to parse, keep serving the previous snapshot and record the failure in metrics and logs.
- FR-013: Support pinning to a specific inventory version for a run; when pinned, ignore notifications for other versions.
- FR-014: Expose Prometheus metrics: request latency, latency per hop (Redis, prediction), bid / no-bid / fallback counts with reasons, active snapshot version, snapshot reload duration and failures.
- FR-015: Expose readiness and liveness endpoints. Readiness requires a loaded snapshot and established Redis connectivity.
- FR-016: Read all runtime settings (variant, deadlines, Go runtime settings including the effective GOMAXPROCS, pool sizes) from configuration and report them in a metadata endpoint consumed by the orchestrator. The metadata endpoint listens on a separate port that is not exposed through the load balancer.
- FR-017: On shutdown, delete the pod's own Pub/Sub subscription. Subscriptions left behind by crashed pods expire after 1 day. The CLI also deletes leftover subscriptions on the snapshot topic at session start. The Pub/Sub emulator does not implement expiration, so this cleanup path is verified only on GKE.

### 3.2 CTR prediction service

- FR-020: Expose a gRPC endpoint that returns a predicted CTR for a bid opportunity, with a versioned proto contract managed by `buf` (contract definition open, see OD-10).
- FR-021: Serve CTR values from a static table of random values generated from the run's seed and loaded into memory at startup. Values are keyed by campaign (optionally also by a request attribute, see OD-10), so identical requests receive identical CTRs across runs with the same seed. Lookups make no network call, so the gRPC hop remains the only cost being measured.
- FR-022: Make latency (base and distribution), variance, error rate and overload behaviour (for example queueing beyond a concurrency limit) configurable.
- FR-023: Expose a control endpoint that changes behaviour at runtime: healthy, slow, partial errors, unavailable. Every change is logged with a timestamp and reported to the orchestrator.
- FR-024: The control endpoint listens on a separate port and Service from the gRPC endpoint. It is not exposed through any load balancer, and a default-deny NetworkPolicy allows only the orchestrator's path to reach it.
- FR-025: The in-process scorer variant uses the same CTR table generated from the same seed, so remote and in-process variants differ only by the network boundary.
- FR-026: Resilience mechanisms (circuit breaker, retry, hedging) are implemented as `Scorer` decorators in the DSP, not through gRPC service config. grpc-go does not support the native hedging policy. Every decorator counts the extra calls it sends.

### 3.3 SSP simulator

- FR-030: Generate open-loop traffic with a seeded generator, written in Go, in three shapes: steady, ramp (including stepped ramp) and burst.
- FR-031: Run on a dedicated on-demand GCE VM in the same region and zone as the GKE cluster, sending traffic through an internal L4 passthrough Network Load Balancer. The bid endpoint has no public address.
- FR-032: Enforce the response deadline, applying the simulated exchange-side network allowance as a deadline reduction. Responses arriving after the deadline count as timeouts.
- FR-033: Run a first-price auction per request: draw a competing clearing price from a seeded, configurable distribution (for example log-normal), apply a floor price, and record the outcome as won, lost, no-bid or timed out.
- FR-034: Record end-to-end latency, measured from each request's intended send time, in an HDR histogram (`hdrhistogram-go`), which is the source of truth for reported latency.
- FR-035: Track fallback-tagged bids separately so fallback share and win rate on fallback bids can be reported.
- FR-036: Detect when the generator falls behind its schedule (coordinated omission or saturation of the VM) and mark the run as invalid in the result file.
- FR-037: Support several SSP instances in parallel, with histograms and counters merged into one result.
- FR-038: Write one immutable JSON result file per run containing percentiles, won / lost / no-bid / timeout counts, fallback counts, per-step results for ramps, the full scenario file, environment metadata and the git SHA.
- FR-039: Before the first reference run on a new SSP machine type, run a calibration against a trivial echo server and a stall test (pause the target mid-run and confirm the stall appears in the histogram). The calibration sets the per-VM throughput limit (OD-12).

### 3.4 Inventory builder

- FR-040: Build inventory from a YAML definition or from a named workload profile (small, medium, large) using seeded, deterministic Zipfian distributions.
- FR-041: Include in each campaign at minimum: targeting attributes, budget, frequency cap, bid parameters and `fallback_bid`.
- FR-042: Produce a versioned snapshot with a checksum. The same input and seed always produce a byte-identical snapshot.
- FR-043: Upload the snapshot to GCS. Only after the upload succeeds, update the `latest` manifest and then publish a message to the Cloud Pub/Sub snapshot topic with the version, GCS path and checksum.
- FR-044: Run on a schedule and on demand from the CLI (schedule and format open, see 4.4).

### 3.5 Orchestration and reporting CLI

- FR-050: Run a scenario end to end with one command (for example `tenx run scenario.yaml`): deploy the variant through Helm, reset state, warm up, measure, apply the fault timeline, collect results, optionally tear down.
- FR-051: Validate the scenario file against its schema before touching any infrastructure.
- FR-052: Reset protocol before each run: flush Redis, reload budgets, pin the inventory version, set budgets high enough not to run out unless exhaustion is under test.
- FR-053: Run a fixed warm-up (default about 2 minutes) and exclude it from results.
- FR-054: Apply the scenario's fault timeline to the prediction service through its control endpoint at the specified offsets, and record phase boundaries in the result file.
- FR-055: Collect environment metadata: GKE version, machine types, node pools, physical host identifiers where GCP exposes them, CPU manager policy, dataplane, `externalTrafficPolicy`, replica counts, Go version, Go runtime settings, image digests, Redis version, inventory version, seed, session ID, git SHA.
- FR-056: Find the maximum sustainable rate from a stepped ramp: the highest step at which the canonical SLO holds for the full step.
- FR-057: Compare two result files and print a difference report; optionally render a static HTML report.
- FR-058: Warn when compared runs differ in environment metadata, inventory version or Go runtime settings beyond the variable under test.
- FR-059: Check reproducibility: compare three or more runs of one configuration from the same session and report whether p99 agrees within 5%. The deviation is measured as the largest relative difference of any run's p99 from the median p99 of the set.
- FR-060: Handle interruption: on abort, restore the prediction service to healthy, stop traffic and mark the partial result as aborted.
- FR-061: Provide inventory commands: generate, validate and publish a snapshot.
- FR-062: Collect CFS throttling counters (`nr_throttled`, `throttled_usec`) for DSP, prediction and Redis containers during each measured window. Any throttling marks the run `invalid: cpu_throttled`.
- FR-063: Run variant comparisons interleaved within one session (for example ABBA blocks, at least 5 runs per variant), with bootstrap confidence intervals on p99. Results from different sessions are not compared directly.
- FR-064: Provide an A/A command that runs one configuration against itself in the same pattern as FR-063 and reports the noise floor, which is the minimum detectable effect. Every experiment report states it.
- FR-065: Tear down on every exit path of `tenx run`, including failure and interrupt, when the teardown flag is set. Teardown scales node pools to zero and destroys the SSP VM.

### 3.6 Scenario file

- FR-070: Declarative YAML containing: traffic shape and rate schedule, workload profile, inventory version, DSP variant and strategy options, prediction behaviour, fault timeline, replica counts, SLO, warm-up duration and seed.
- FR-071: Scenario files are versioned and stored in the monorepo. A new scenario requires no change to the traffic generator or the measurement layer.

### 3.7 Observation

- FR-080: Prometheus scrapes DSP, prediction service and Redis metrics.
- FR-081: A Grafana dashboard provisioned from the repository shows live latency, throughput, fallback rate and active snapshot version, with inventory reloads and fault phases as annotations. Inclusion in the MVP is pending confirmation (see 4.4). Live observation is diagnostic only; reported numbers come from the SSP result file.

### 3.8 Infrastructure

- FR-090: One persistent zonal GKE Standard cluster with:
  - dedicated node pools, fixed machine types and a pinned GKE version;
  - whole-number CPU requests equal to limits and the static CPU manager policy;
  - compact placement for the DSP, prediction and Redis pools;
  - Dataplane V2 with NetworkPolicy, enabled at cluster creation;
  - HPA and cluster autoscaling disabled.
- FR-091: Terraform is split into two layers:
  - **Persistent layer, created once:** network, cluster control plane, GCS buckets (snapshots, results, Terraform state, all versioned), the Pub/Sub snapshot topic, Artifact Registry and IAM.
  - **Session layer, per experiment session:** node pool sizes and the SSP VM. These are scaled up at session start and scaled to zero or destroyed afterwards to keep costs predictable.

  The GKE free-tier credit covers the control-plane fee of one zonal cluster, so keeping the cluster costs close to nothing while idle.
- FR-092: Our own Helm charts for all in-cluster services, including Redis 8 from the official image pinned by digest. Third-party charts such as Bitnami are not used.
- FR-093: A local development setup with Docker Compose runs the stack for functional checks. It uses the Pub/Sub emulator in place of Cloud Pub/Sub and fake-gcs-server or a local directory in place of GCS. Results produced locally are marked non-reference.
- FR-094: Reference runs use on-demand VMs. Spot VMs are allowed only for development sessions, because a preemption mid-run invalidates the measurement.

### 3.9 Access control

- FR-095: No application-level authentication or user roles (out of scope). Access is restricted at the infrastructure level, overriding insecure GCP defaults explicitly:
  - GCP IAM controls who can create clusters, VMs, buckets and topics. No service account keys are created; this is enforced by discipline or by an org policy where an organization exists.
  - The cluster uses private nodes, the DNS-based control-plane endpoint and a custom least-privilege node service account instead of the Compute default service account.
  - Pods reach GCS and Pub/Sub through Workload Identity Federation for GKE. The DSP's identity has:
    - read access to the snapshot bucket;
    - a custom role limited to creating, consuming and deleting subscriptions, with `pubsub.topics.attachSubscription` on the snapshot topic only.
  - The bid endpoint is exposed only through an internal passthrough NLB. If an external NLB is ever used, `loadBalancerSourceRanges` restricts it to the SSP VM.
  - A default-deny NetworkPolicy allows only the required paths: SSP to DSP, DSP to prediction, DSP to Redis, and the orchestrator to the control and metadata endpoints.
  - Redis runs a patched 8.x release pinned by digest (at least 8.10.1, covering CVE-2025-49844 and CVE-2026-81934), with an ACL user for the DSP, the default user disabled and no exposure outside the cluster.
  - The SSP VM has no public SSH. Access goes through IAP with OS Login.
  - Grafana and Prometheus are reached through port-forwarding rather than a public address.
  - Terraform state lives in a private, versioned GCS bucket in the persistent layer. Secrets are kept out of state.
- FR-096: CI/CD on GitHub Actions:
  - Builds, tests, runs `govulncheck` and pushes images to Artifact Registry.
  - Authenticates to GCP through keyless Workload Identity Federation, restricted by numeric `repository_owner_id` and `repository_id`.
  - Pins every action to a commit SHA and never uses `pull_request_target`.
  - Uses a CI identity that can only push images. `terraform apply` runs from the developer's own credentials, never from CI.
  - Renovate opens grouped dependency updates for Go modules, actions, base images, Helm charts and Terraform providers.
- FR-097: Cost guardrails:
  - A monthly GCP budget with alerts at 50% and 90% (amount set in OD-03).
  - TTL labels on all session resources.
  - A scheduled cleanup job that scales node pools to zero and deletes the SSP VM after the TTL expires.

### 3.10 Experiment reporting

- FR-100: Each experiment has a Markdown report with a hypothesis recorded before the run, setup, result files referenced by ID, a single actionable number and conclusions.

## 4. Product Boundaries

### 4.1 In scope for the MVP

- The five components in 1.4, the shared `core` package and the `Scorer` interface.
- GKE as the single reference environment; local runs for development only.
- Synthetic traffic only.
- Milestones M1 to M4, including pattern pairs a to e.
- CLI-based inventory definition, run execution, run comparison and HTML report.

### 4.2 Out of scope for the MVP

- Integration with real exchanges, full OpenRTB compliance, certification.
- Real money: billing, invoicing, payouts, advertiser accounts.
- Creative storage and serving, ad rendering, CDN concerns.
- Consent management, fraud detection, brand safety, viewability.
- Real machine learning; the prediction service returns static random CTR values and simulates behaviour only.
- Attribution, long-term reporting, data warehousing.
- Multi-tenancy, application authentication, user roles, access control beyond infrastructure-level restrictions.
- Multi-region deployment, autoscaling, production-grade operations, unless topology itself is the variable under test.
- Custom web UI dashboard (moved to post-MVP; replaced by CLI and Grafana).
- Comparing monorepo with microservices (backlog).
- Visual polish; outputs must be readable and honest.

### 4.3 Constraints and assumptions

- Solo developer, no deadline, cost-sensitive cloud usage.
- Only GKE results are reported. Local results are never published.
- All reported latency comes from SSP-side HDR histograms; internal metrics and tracing are diagnostic.
- Capacity is measured per DSP pod first; horizontal scaling is a later experiment.
- Budgets must not be exhausted during a run unless exhaustion is under test.
- Variant comparisons are made only between runs from the same session, because fresh nodes land on different physical hosts.

### 4.4 Open decisions

| ID | Decision | Status | Blocks |
|---|---|---|---|
| OD-01 | SLO values: p99 threshold, maximum timeout rate, step duration, network allowance size | Open | M1 |
| OD-02 | Prediction deadline cap (15 to 20 ms proposed) and maximum share of remaining budget | Open | M2 |
| OD-03 | Monthly GCP budget, which sets machine types, pool sizes and run duration | Open. Proposed: $100–150 a month (a lean topology costs about $1.8–2.3 per running hour) | M1 |
| OD-04 | Local environment: minikube or Docker Compose, and how much of the stack must run locally | Resolved: Docker Compose with the DSP, prediction service, Redis, Go SSP, Pub/Sub emulator and fake-gcs-server (FR-093) | M1 |
| OD-05 | Whether Prometheus and Grafana with annotations are part of the MVP | Open. Proposed: metrics endpoints and pprof in the MVP, with the Grafana dashboard deferred unless needed for diagnosis | M1 |
| OD-06 | Final confirmation of Terraform and the orchestrator command surface | Terraform resolved: two layers (FR-091). Command surface still open | M1 |
| OD-07 | Snapshot format (Protobuf or JSON), builder schedule, reload interval | Open | M1 |
| OD-08 | Redis: in-cluster (Helm) or Memorystore, node placement, key design for frequency caps | Resolved: Redis 8 in the cluster from our own chart on a dedicated node pool. Memorystore for Redis offers only up to 7.2. Frequency-cap key design still open; `INCR` plus `EXPIRE` per user and campaign is the portable default | M1 |
| OD-09 | Workload profile parameters: campaign counts, user ID and attribute cardinality, Zipf exponents | Open | M1 |
| OD-10 | CTR simulation: dependence on request and campaign attributes, bid derivation from CTR (for example CTR × value per click), gRPC proto contract, mock table key (campaign only or campaign plus a request attribute) and value range | Open | M1 |
| OD-11 | Clearing-price distribution, floor prices, whether fallback price deviation is a formal metric | Open | M3 |
| OD-12 | Per-VM SSP throughput and the threshold for multiple SSP instances | Method resolved: calibration run (FR-039). Values open | M1 |
| OD-13 | Repository visibility, licence, where reports are published | Open. Note: on the GitHub Free plan, environment protection rules work only in public repositories | M4 |
| OD-14 | DSP behaviour on Redis failure (no-bid proposed in FR-006) | Open | M1 |

### 4.5 Post-MVP backlog

Batched predictions, JSON codec comparison, connection pool sizing, GC tuning, horizontal scaling, monorepo vs microservices, custom web dashboard, internal tracing. Added in 1.2:
- bitmap index (`kelindar/bitmap`) vs linear scan by workload profile;
- auto-pipelining Redis client (`rueidis`) as a pair-d variant;
- in-cluster Redis vs Memorystore for Valkey;
- bare GCE VMs vs default GKE vs tuned GKE (what Kubernetes adds to an RPC boundary);
- snapshot hot-reload cost under load.

## 5. User Stories

The single persona is the researcher (the author), acting at times as developer, operator and portfolio author.

### 5.1 Environment and access

#### US-001

- ID: US-001
- Title: Provision the reference environment
- Description: As the researcher, I want to create the persistent environment once and start each experiment session with one command so that every session starts from an identical environment.
- Acceptance Criteria:
  - A one-time command applies the persistent Terraform layer: network, a single-zone GKE Standard cluster, versioned GCS buckets (snapshots, results, Terraform state), the Pub/Sub snapshot topic, Artifact Registry and IAM.
  - A session command applies the session layer: it scales the defined node pools up and creates the on-demand SSP VM in the same zone.
  - Machine types, node counts, zone, GKE version and CPU manager policy come from versioned configuration, not manual input.
  - HPA and cluster autoscaling are disabled; private nodes, Dataplane V2 and compact placement are enabled.
  - Running either command twice without changes produces no infrastructure changes.

#### US-002

- ID: US-002
- Title: Tear down the reference environment
- Description: As the researcher, I want to release the session's cloud resources after work so that costs stay predictable.
- Acceptance Criteria:
  - A single command scales all node pools to zero and destroys the SSP VM.
  - Result files, snapshots, the Pub/Sub topic and Terraform state live in the persistent layer and remain available afterwards.
  - The command reports which resources were removed or scaled down and fails loudly if any billable session resource remains.
  - A separate, explicitly confirmed command destroys the persistent layer.

#### US-003

- ID: US-003
- Title: Run the stack locally for development
- Description: As the researcher, I want to run the services locally so that I can check functional behaviour without cloud cost.
- Acceptance Criteria:
  - The local Docker Compose setup starts the DSP, prediction service, Redis, the Go SSP, the Pub/Sub emulator and fake-gcs-server or equivalent storage.
  - A local scenario run completes and produces a result file.
  - Result files from local runs carry an environment marker `non-reference`, and the compare command warns when one is compared against a GKE run.

#### US-004

- ID: US-004
- Title: Restrict access to infrastructure and control endpoints
- Description: As the researcher, I want only authorised identities and in-cluster callers to reach infrastructure and control surfaces so that a public testbed cannot be altered or abused by others.
- Acceptance Criteria:
  - Creating or destroying resources requires GCP IAM credentials; no credentials are stored in the repository.
  - Pods access GCS and Pub/Sub through Workload Identity Federation for GKE; no service account key files exist in the cluster or the repository.
  - The bid endpoint is reachable only through the internal load balancer; a request from outside the VPC fails.
  - The prediction service control endpoint and the DSP metadata endpoint are not reachable from outside the cluster, nor from the SSP VM inside the VPC (verified by requests from both failing).
  - Cluster nodes have no external IP addresses; the SSP VM is reachable only through IAP.
  - Redis accepts connections only from DSP pods and rejects the default user.
  - GitHub Actions authenticates without stored keys and can only push images.
  - Grafana and Prometheus have no public address and are reached through port-forwarding.

### 5.2 Running scenarios

#### US-005

- ID: US-005
- Title: Run a scenario with one command
- Description: As the researcher, I want to run a scenario file with one command so that a full experiment executes without manual steps.
- Acceptance Criteria:
  - `tenx run <scenario.yaml>` deploys the variant through Helm, resets state, warms up, measures, applies the fault timeline and collects results.
  - The command ends by printing the path to the result file and a summary (p50, p99, p99.9, timeout rate, win rate, fallback share).
  - No manual kubectl, Helm or Redis command is required at any point.
  - The command exits with a non-zero code if any stage fails, naming the stage.

#### US-006

- ID: US-006
- Title: Validate a scenario file before running
- Description: As the researcher, I want invalid scenario files rejected before deployment so that I do not pay for a broken run.
- Acceptance Criteria:
  - Missing required fields, unknown fields, unknown variants and invalid fault timelines are reported with field paths.
  - Validation completes before any infrastructure call.
  - A `validate` command performs the same check without running anything.

#### US-007

- ID: US-007
- Title: Optionally tear down after a run
- Description: As the researcher, I want a flag that tears down session resources after a run so that an unattended run does not leave resources running.
- Acceptance Criteria:
  - With the teardown flag, node pools are scaled to zero and the SSP VM is destroyed after results are collected and saved.
  - Teardown happens even if the measurement stage fails or the run is interrupted, after partial results are saved.
  - Without the flag, session resources are left running, carry a TTL label, and are removed by the scheduled cleanup job when the TTL expires.

#### US-008

- ID: US-008
- Title: Abort a running scenario safely
- Description: As the researcher, I want to interrupt a run and leave the system in a known state.
- Acceptance Criteria:
  - On interrupt, traffic stops, the prediction service is restored to healthy, and a partial result file marked `aborted` is written.
  - Aborted results are excluded from reproducibility checks and comparisons unless explicitly included.

#### US-009

- ID: US-009
- Title: Reset state before each run
- Description: As the researcher, I want state reset automatically so that runs do not influence each other.
- Acceptance Criteria:
  - Redis is flushed and budgets and frequency caps are reloaded from the pinned snapshot before warm-up.
  - Budgets are set high enough that none run out during the run unless the scenario marks exhaustion as under test.
  - The result file records that the reset completed and when.

#### US-010

- ID: US-010
- Title: Exclude warm-up from results
- Description: As the researcher, I want a warm-up period excluded from measurement so that cold caches and JIT-like effects do not distort results.
- Acceptance Criteria:
  - Warm-up duration comes from the scenario file (default about 2 minutes).
  - No request sent during warm-up appears in the result histograms or counters.
  - The result file records the warm-up duration.

#### US-011

- ID: US-011
- Title: Record environment metadata
- Description: As the researcher, I want every result to carry full environment metadata so that any number can be traced to its exact configuration.
- Acceptance Criteria:
  - The result file includes GKE version, machine types, node pools, replica counts, Go version, GOMAXPROCS, GOGC, GOMEMLIMIT, pool sizes, image digests, inventory version, seed, git SHA and the full scenario file.
  - A run with uncommitted changes in the repository is flagged as `dirty` in the metadata.
  - Result files cannot be overwritten by later runs; each has a unique ID.

### 5.3 Reproducibility and capacity

#### US-012

- ID: US-012
- Title: Verify reproducibility
- Description: As the researcher, I want to confirm that three consecutive runs of one configuration agree on p99 within 5% so that differences between variants can be trusted.
- Acceptance Criteria:
  - A command takes three or more result files and reports the maximum relative deviation of any run's p99 from the median p99, and PASS or FAIL against the 5% threshold.
  - The command refuses to evaluate runs with different scenario content, environment metadata, inventory version or session ID.
  - Runs from non-reference environments and runs marked invalid (`generator_saturated`, `cpu_throttled`) are rejected for this check.

#### US-013

- ID: US-013
- Title: Find the maximum sustainable rate
- Description: As the researcher, I want a stepped ramp to report the highest rate at which the SLO holds so that each configuration has a capacity figure, not a latency at comfortable load.
- Acceptance Criteria:
  - The scenario defines start rate, step size, step duration and the canonical SLO.
  - The result file contains per-step p99, timeout rate and SLO verdict.
  - The reported maximum sustainable rate is the highest step where the SLO held for the full step.
  - If the SLO fails on the first step, the result states that no sustainable rate was found.
  - Capacity is reported per DSP pod, with replica count recorded.

#### US-014

- ID: US-014
- Title: Detect an invalid run caused by the load generator
- Description: As the researcher, I want runs flagged when the SSP cannot keep its schedule so that I never report numbers limited by the generator rather than the system.
- Acceptance Criteria:
  - The SSP tracks scheduled versus actual send times and its own CPU saturation.
  - If lag exceeds a configured threshold, the result file is marked `invalid: generator_saturated`.
  - Invalid runs are rejected by the reproducibility check and flagged by the compare command.
  - Before the first reference run on a new SSP machine type, the echo-server calibration and stall test (FR-039) pass, and their results are stored with the session.

#### US-015

- ID: US-015
- Title: Run multiple SSP instances
- Description: As the researcher, I want several SSP instances to share the load when one VM is insufficient so that high target rates remain reachable.
- Acceptance Criteria:
  - The scenario specifies the number of SSP instances; the target rate is divided between them.
  - All instances use derived seeds so the combined traffic is deterministic.
  - Histograms and counters are merged into a single result file, with per-instance details kept.

### 5.4 Bidding behaviour

#### US-016

- ID: US-016
- Title: Bid on a matching request
- Description: As the researcher, I want the DSP to return a bid when a campaign matches so that the workload reflects realistic bidding.
- Acceptance Criteria:
  - A request matching a campaign's targeting, within budget and frequency cap, receives a bid with price derived from the score.
  - The response arrives within the hard time budget.
  - The bid is counted in DSP metrics and in the SSP result as won or lost according to the auction.

#### US-017

- ID: US-017
- Title: Return a no-bid when nothing matches
- Description: As the researcher, I want explicit no-bids with reasons so that no-bid rates are explainable.
- Acceptance Criteria:
  - A request matching no campaign receives a no-bid response.
  - The reason (`no_match`, `budget_exhausted`, `frequency_capped`, `state_unavailable`) is counted in metrics.
  - The SSP counts no-bids separately from losses and timeouts.

#### US-018

- ID: US-018
- Title: Enforce campaign budgets
- Description: As the researcher, I want budgets enforced through Redis so that state access is part of the measured critical path.
- Acceptance Criteria:
  - A campaign whose budget is exhausted is not bid on.
  - Budget checks and updates for one request happen in a single Redis round-trip.
  - A scenario that sets a low budget produces `budget_exhausted` no-bids after the budget is spent.

#### US-019

- ID: US-019
- Title: Enforce frequency caps
- Description: As the researcher, I want frequency caps per user and campaign enforced so that per-user state is exercised.
- Acceptance Criteria:
  - After a user reaches the cap for a campaign, further requests for that user and campaign receive no bid from that campaign.
  - The frequency cap check is part of the same Redis round-trip as the budget check.
  - Redis latency per request is recorded as a separate metric.

#### US-020

- ID: US-020
- Title: Handle malformed or unsupported requests
- Description: As the researcher, I want invalid requests rejected quickly so that they do not distort latency or crash the service.
- Acceptance Criteria:
  - Malformed JSON, missing required fields or an unsupported schema version result in an error response within the time budget.
  - Rejections are counted by reason in metrics.
  - The DSP keeps serving other requests normally.

#### US-021

- ID: US-021
- Title: Respect the hard time budget
- Description: As the researcher, I want the DSP to answer within the budget derived from `tmax` so that late responses are a measured outcome, not an accident.
- Acceptance Criteria:
  - The time budget equals `tmax` minus the configured network allowance.
  - When the budget is close to expiring, the DSP returns its best available answer (bid, fallback bid or no-bid) rather than waiting.
  - A request with a missing or invalid `tmax` uses a configured default and is counted.

#### US-022

- ID: US-022
- Title: Handle Redis unavailability
- Description: As the researcher, I want defined behaviour when Redis is slow or down so that a failing state store produces a measurable outcome.
- Acceptance Criteria:
  - On Redis error or timeout, the DSP responds within the time budget with a no-bid and reason `state_unavailable` (pending confirmation in OD-14).
  - The share of `state_unavailable` responses is visible in metrics.
  - When Redis recovers, normal bidding resumes without restarting the DSP.

### 5.5 Prediction and variants

#### US-023

- ID: US-023
- Title: Select the scorer variant by configuration
- Description: As the researcher, I want to choose remote or in-process prediction at startup so that both run from the same image and the same bidding code.
- Acceptance Criteria:
  - The scenario's variant field selects the `Scorer` implementation; no rebuild is required.
  - An unknown variant name fails startup with a clear error.
  - The active variant appears in DSP metadata and in the result file.

#### US-024

- ID: US-024
- Title: Bound the prediction call with a derived deadline
- Description: As the researcher, I want the prediction call limited by the remaining time budget so that a slow dependency cannot cause a timeout at the exchange.
- Acceptance Criteria:
  - The gRPC deadline equals the smaller of the configured cap and the share of remaining budget allowed.
  - When the prediction service exceeds the deadline, the call is cancelled and the DSP proceeds with a fallback bid.
  - Prediction latency is recorded as a separate metric.

#### US-025

- ID: US-025
- Title: Bid with a fallback value when prediction fails
- Description: As the researcher, I want the DSP to keep bidding with each campaign's `fallback_bid` when prediction is unavailable so that business continuity under degradation can be measured.
- Acceptance Criteria:
  - On prediction error, timeout or rejection by a resilience mechanism, the bid price equals the campaign's `fallback_bid` from the snapshot.
  - Fallback bids are tagged in DSP metrics and visible to the SSP result.
  - The result file reports fallback share, win rate on fallback bids and timeout rate.

#### US-026

- ID: US-026
- Title: Configure prediction service behaviour
- Description: As the researcher, I want to set the prediction service's latency, variance, error rate and overload behaviour so that it acts as a controlled experimental variable.
- Acceptance Criteria:
  - The scenario file sets base latency, latency distribution, error rate and concurrency limit.
  - Measured prediction latency in a healthy run matches the configured distribution within a documented tolerance.
  - Scores are deterministic for a given request, campaign and seed.

#### US-027

- ID: US-027
- Title: Apply a fault timeline
- Description: As the researcher, I want fault phases applied at defined times so that degradation scenarios are repeatable.
- Acceptance Criteria:
  - The scenario file defines phases (for example healthy, slow, partial errors, unavailable, recovery) with start offsets and parameters.
  - The orchestrator applies each phase through the control endpoint at the specified offset.
  - Phase boundaries are recorded in the result file and, when Grafana is enabled, shown as annotations.
  - Results are reported per phase as well as for the whole run.

#### US-028

- ID: US-028
- Title: Measure recovery after a fault
- Description: As the researcher, I want recovery time measured so that I know how long the system stays degraded after the dependency heals.
- Acceptance Criteria:
  - Recovery time is the interval from the start of the recovery phase until fallback rate and p99 return within a configured tolerance of the healthy baseline.
  - The result file reports recovery time or states that recovery did not complete within the phase.

#### US-029

- ID: US-029
- Title: Document degradation outcomes
- Description: As the researcher, I want each degradation mode to produce a documented outcome so that behaviour is measured, not assumed.
- Acceptance Criteria:
  - For slow, partial errors and unavailable, the result reports fallback rate, timeout rate, win rate, p99 and recovery time.
  - The report states whether the DSP kept bidding in each phase and on what basis (prediction or fallback).

#### US-030

- ID: US-030
- Title: Quantify the cost of the service split
- Description: As the researcher, I want remote and in-process prediction compared across load levels so that the cost of the service boundary is a number.
- Acceptance Criteria:
  - The same workload and seed run with each variant at three or more load levels.
  - The comparison reports the difference in p99, timeout rate and maximum sustainable rate per load level.
  - Both variants run identical `core` bidding code (verified by the same git SHA and image digest).

### 5.6 Traffic and auction

#### US-031

- ID: US-031
- Title: Generate traffic in a chosen shape
- Description: As the researcher, I want steady, ramp and burst traffic so that I can test both sustained load and spikes.
- Acceptance Criteria:
  - Each shape is configurable by rate, duration and shape parameters.
  - Traffic is open-loop: send times follow the schedule regardless of response times.
  - The same seed produces the same request sequence.

#### US-032

- ID: US-032
- Title: Enforce the exchange deadline
- Description: As the researcher, I want late responses counted as timeouts so that tail latency translates into lost auctions.
- Acceptance Criteria:
  - The SSP applies `tmax` minus the network allowance as its deadline.
  - Responses after the deadline are counted as timeouts and do not take part in the auction.
  - Connection errors are counted separately from timeouts.

#### US-033

- ID: US-033
- Title: Run the auction
- Description: As the researcher, I want each bid to compete against a simulated clearing price so that win rate reflects bid quality, including fallback bids.
- Acceptance Criteria:
  - A clearing price is drawn per request from a seeded, configurable distribution.
  - Bids below the floor price lose.
  - First-price rules decide the outcome; won, lost, no-bid and timeout counts are recorded.
  - The same seed produces the same clearing prices.

#### US-034

- ID: US-034
- Title: Write an immutable result file
- Description: As the researcher, I want each run saved as a single immutable JSON file so that results are permanent, comparable evidence.
- Acceptance Criteria:
  - The file contains percentiles from the HDR histogram, all outcome counts, per-step and per-phase results, the scenario file, environment metadata and git SHA.
  - Writing to an existing result ID fails.
  - The file validates against a versioned result schema.

### 5.7 Inventory

#### US-035

- ID: US-035
- Title: Generate inventory from a workload profile
- Description: As the researcher, I want to generate inventory from a named profile so that workloads of known size are reproducible.
- Acceptance Criteria:
  - Profiles small, medium and large are available with documented parameters.
  - Generation is seeded and deterministic: the same profile and seed produce a byte-identical snapshot.
  - Each campaign includes targeting, budget, frequency cap, bid parameters and `fallback_bid`.

#### US-036

- ID: US-036
- Title: Define inventory in YAML
- Description: As the researcher, I want to write inventory by hand in YAML so that edge cases can be tested precisely.
- Acceptance Criteria:
  - The CLI validates a YAML inventory and reports errors with field paths.
  - A valid YAML inventory produces a snapshot with the same structure as a generated one.
  - Campaigns missing `fallback_bid` are rejected.

#### US-037

- ID: US-037
- Title: Publish a snapshot
- Description: As the researcher, I want to publish a snapshot so that every DSP pod uses the same inventory.
- Acceptance Criteria:
  - Publishing uploads the snapshot to GCS, updates the `latest` manifest and then publishes a Cloud Pub/Sub message with version, path and checksum.
  - The manifest is updated only after the upload succeeds.
  - The inventory builder also publishes on its configured schedule.

#### US-038

- ID: US-038
- Title: Hot-reload a new snapshot
- Description: As the researcher, I want the DSP to swap in a new snapshot without pausing so that reload cost can be measured.
- Acceptance Criteria:
  - On a Pub/Sub notification for a newer version, the DSP loads and verifies the snapshot in the background and swaps it atomically.
  - Each pod receives every notification through its own subscription.
  - No request fails or is handled with a partially loaded snapshot during the swap.
  - Reload duration and the new active version are exposed as metrics; reload events are recorded with timestamps.

#### US-039

- ID: US-039
- Title: Recover from a missed notification
- Description: As the researcher, I want a pod that starts or restarts to load the current snapshot even if it missed a Pub/Sub message.
- Acceptance Criteria:
  - At startup the DSP first creates its own subscription and then reads the `latest` manifest from GCS and loads the referenced snapshot, so a version published in between is still delivered.
  - The pod is not ready until a snapshot is loaded.
  - If Pub/Sub is unavailable, the DSP keeps serving its current snapshot and logs the condition.
  - A test publishes a version between subscription creation and the manifest read and confirms the pod ends up on the newer version.

#### US-040

- ID: US-040
- Title: Reject a corrupted snapshot
- Description: As the researcher, I want a broken snapshot rejected so that a bad publish cannot take the DSP down.
- Acceptance Criteria:
  - A checksum mismatch, failed download or parse error leaves the previous snapshot active.
  - The failure is counted in metrics and logged with the version.
  - A pod that cannot load any snapshot at startup does not become ready.

#### US-041

- ID: US-041
- Title: Pin the inventory version for a run
- Description: As the researcher, I want a run to use a fixed inventory version so that compared runs have equal footing.
- Acceptance Criteria:
  - The scenario file names the inventory version.
  - During a pinned run, notifications for other versions are ignored unless a reload is part of the scenario.
  - The result file records the version actually served.

### 5.8 Observation and reporting

#### US-042

- ID: US-042
- Title: Expose service metrics
- Description: As the researcher, I want Prometheus metrics from all services so that I can diagnose why a run behaved as it did.
- Acceptance Criteria:
  - The DSP exposes request latency, latency per hop, Redis latency, bid / no-bid / fallback counts by reason, active snapshot version and reload duration.
  - The prediction service exposes request rate, latency, errors and current fault phase.
  - Metric names and labels are documented.

#### US-043

- ID: US-043
- Title: Watch a run live in Grafana
- Description: As the researcher, I want a provisioned dashboard with annotations so that I can see reloads and fault phases against latency while a run is in progress.
- Acceptance Criteria:
  - The dashboard is provisioned from the repository with no manual setup.
  - Latency, throughput, fallback rate and snapshot version are shown live.
  - Inventory reloads and fault phases appear as annotations at the correct times.
  - This story applies if OD-05 confirms Grafana in the MVP.

#### US-044

- ID: US-044
- Title: Compare two runs
- Description: As the researcher, I want a CLI comparison of two result files so that I can see the difference a variant makes.
- Acceptance Criteria:
  - The command prints side-by-side percentiles, outcome rates, fallback share and maximum sustainable rate, with absolute and relative differences.
  - It warns when runs differ in anything other than the declared variable (environment, inventory, runtime settings, seed).
  - It flags runs that are aborted, invalid or non-reference.

#### US-045

- ID: US-045
- Title: Produce a static HTML report
- Description: As the researcher, I want an HTML report from one or two result files so that results can be shared and linked from experiment reports.
- Acceptance Criteria:
  - The report contains latency percentile charts, per-step and per-phase tables, and environment metadata.
  - The report is a single static file that opens without a server.
  - Every number in the report is traceable to a field in a named result file.

### 5.9 Pattern experiments

#### US-046

- ID: US-046
- Title: Add a new pattern variant in under an hour
- Description: As the researcher, I want to add a variant by implementing an interface and writing a scenario so that experimentation stays cheap.
- Acceptance Criteria:
  - A new `Scorer` or strategy implementation and a scenario file are the only required changes.
  - No change is made to the SSP simulator or the measurement layer.
  - The time from start to first valid run is recorded for each new variant and is under one hour.

#### US-047

- ID: US-047
- Title: Pattern pair a, derived deadline vs fixed or missing timeout
- Description: As the researcher, I want to compare a deadline derived from the remaining budget with a fixed or missing timeout so that the value of budget-aware deadlines is quantified.
- Acceptance Criteria:
  - All three variants run under the same workload and fault timeline.
  - The report gives one actionable number (for example timeout rate during the slow phase).

#### US-048

- ID: US-048
- Title: Pattern pair b, circuit breaker vs none
- Description: As the researcher, I want to compare a circuit breaker with no breaker under dependency failure.
- Acceptance Criteria:
  - Both variants run the same partial-errors and unavailable phases.
  - The report gives one actionable number (for example p99 or recovery time during and after the outage), and records fallback share.

#### US-049

- ID: US-049
- Title: Pattern pair c, retry vs hedged request vs no retry
- Description: As the researcher, I want to compare retry, hedging and no retry so that their effect on tail latency and load amplification is known.
- Acceptance Criteria:
  - All variants run at the same load levels with a slow or partially failing dependency.
  - Retry and hedging are implemented as `Scorer` decorators (FR-026), because grpc-go has no native hedging policy.
  - The report gives one actionable number and records the extra load sent to the prediction service, compared with the roughly 5% extra load reported in "The Tail at Scale" for hedges sent at p95.

#### US-050

- ID: US-050
- Title: Pattern pair d, sync Redis per request vs local counters with async sync
- Description: As the researcher, I want to compare synchronous Redis access with local counters synced asynchronously so that the latency gain and accuracy cost are known.
- Acceptance Criteria:
  - Both variants run the same workload and budgets.
  - The report gives one actionable number for latency or capacity and reports budget overspend or frequency cap violations caused by async sync.

#### US-051

- ID: US-051
- Title: Pattern pair e, synchronous hot-path logging vs async or sampled logging
- Description: As the researcher, I want to compare logging strategies so that the cost of logging on the critical path is known.
- Acceptance Criteria:
  - Variants run at the same load levels with the same log content, as `log/slog` handlers (synchronous, asynchronous, sampled) behind one logging interface.
  - The report gives one actionable number (for example change in maximum sustainable rate) and the share of log lines dropped by sampling or buffering.

#### US-052

- ID: US-052
- Title: Publish an experiment report
- Description: As a portfolio author, I want each experiment written up with a hypothesis recorded before the run so that results are honest and readable.
- Acceptance Criteria:
  - The report template contains hypothesis, setup, result file IDs, the session's noise floor (FR-064), one actionable number and conclusions.
  - The hypothesis is committed to the repository before the first run of the experiment (verified by commit time versus result file time).
  - The report states whether the result confirmed, contradicted or complicated the hypothesis, and whether the measured effect exceeds the noise floor.

#### US-053

- ID: US-053
- Title: Invalidate runs affected by CPU throttling
- Description: As the researcher, I want runs flagged when containers on the measured path are CPU-throttled so that the cost of a service boundary is not confused with quota effects.
- Acceptance Criteria:
  - The orchestrator records CFS throttling counters for DSP, prediction and Redis containers at the start and end of the measured window.
  - Any throttling during the measured window marks the result file `invalid: cpu_throttled`, with the counters included.
  - Invalid runs are rejected by the reproducibility check and flagged by the compare command.

#### US-054

- ID: US-054
- Title: Measure the noise floor
- Description: As the researcher, I want to know the smallest effect a session can detect so that I never report a difference that is within the noise.
- Acceptance Criteria:
  - An A/A command runs one configuration against itself in interleaved blocks within one session (at least 5 runs per arm).
  - It reports bootstrap confidence intervals on p99 and the resulting minimum detectable effect.
  - The compare command shows the noise floor next to every difference and marks differences below it as not significant.

#### US-055

- ID: US-055
- Title: Guard against forgotten cloud resources
- Description: As the researcher, I want session resources to expire automatically so that a forgotten environment cannot run up a large bill.
- Acceptance Criteria:
  - All session resources carry a TTL label set by the session command.
  - A scheduled cleanup job scales node pools to zero and deletes the SSP VM once the TTL has passed.
  - A monthly budget with alerts at 50% and 90% exists in the persistent layer.

## 6. Success Metrics

### 6.1 MVP success criteria

| ID | Criterion | Target | How it is measured |
|---|---|---|---|
| SM-01 | Single-command runs | 100% of scenarios run with one command | Every scenario in the repository completes through `tenx run` with no manual step |
| SM-02 | Reproducibility | p99 within 5% of the median across 3 consecutive runs in one session | Reproducibility check on valid GKE result files with identical configuration and session ID |
| SM-09 | Noise floor | Minimum detectable p99 effect known for every reported comparison | A/A measurement (FR-064) in the same session as the comparison |
| SM-03 | Capacity | One documented maximum sustainable rate per configuration | Stepped ramp under the canonical SLO, per DSP pod |
| SM-04 | Cost of the split | Remote vs in-process quantified at 3 or more load levels | Difference in p99, timeout rate and capacity from SSP-side HDR histograms |
| SM-05 | Degradation | Documented outcome for slow, partial errors and unavailable | Fallback rate, timeout rate, win rate, p99 and recovery time per phase |
| SM-06 | Pattern pairs | 5 pairs (a to e) with results | One published report per pair with one actionable number |
| SM-07 | Learning test | At least 1 result that contradicts or complicates a prior belief | Pre-run hypothesis versus result in published reports |
| SM-08 | Extensibility | New scenario in under 1 hour | Time recorded for each pattern variant added; no changes to SSP or measurement code |

### 6.2 Run validity metrics

Applied to every reported run. A run that fails any of these is not reported.

- Generator lag stays below the configured threshold (no `generator_saturated` flag).
- No CPU throttling on the measured path during the measured window (no `cpu_throttled` flag).
- Run comes from the GKE reference environment on on-demand VMs, and the repository was clean.
- The SSP calibration (FR-039) passed for the SSP machine type in use.
- Warm-up excluded and reset protocol completed.
- No budget exhausted unless under test.
- Comparisons use runs from one session only (FR-063).

### 6.3 Per-run metrics reported

- Latency: p50, p90, p99, p99.9, max (SSP-side, end to end).
- Outcomes: bid, no-bid by reason, won, lost, timeout, connection error rates.
- Win rate overall and on fallback bids.
- Fallback share.
- Per-hop diagnostics: Redis latency, prediction latency.
- Maximum sustainable rate (for stepped ramps).
- Recovery time (for fault timelines).

### 6.4 Milestone tracking

| Milestone | Done when |
|---|---|
| M1 | SM-01 and SM-02 met for the baseline configuration; SSP calibration passed; OD-01, OD-03, OD-07 to OD-10, OD-12 and OD-14 resolved |
| M2 | SM-03, SM-04 and SM-09 met |
| M3 | SM-05 met |
| M4 | SM-06 and SM-08 met; SM-07 checked across all published reports |
