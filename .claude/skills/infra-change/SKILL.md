---
name: infra-change
description: Rules for changing infrastructure — Terraform (persistent and session layers), own Helm charts, NetworkPolicy, IAM and Workload Identity, GitHub Actions CI, Docker Compose. Use whenever editing files under terraform/, deploy/helm/, .github/, docker-compose.yaml or Dockerfile.
---

# Infrastructure changes

The reference environment is part of the measurement. An unnoticed infra change silently invalidates
comparisons, and a forgotten resource costs money. Read PRD sections 1.5, 3.8 and 3.9 before larger changes.

## Safety

- **Never** run `terraform apply`, `terraform destroy`, `helm install/upgrade/uninstall` against GKE, `gcloud ... create/delete`, or `tenx run` on GKE without explicit confirmation in chat. `terraform plan`, `helm template`, `helm lint` are fine.
- Never create service account keys, never commit secrets, never read `.env`.
- `terraform apply` runs from the developer's credentials only, never from CI.

## Terraform

- Two layers: `terraform/persistent` (network, cluster control plane, versioned GCS buckets, Pub/Sub topic, Artifact Registry, IAM, budget alerts) and `terraform/session` (node pool sizes, SSP VM, TTL labels).
- Session resources must carry a TTL label; reference resources use on-demand VMs (Spot only for dev).
- Pin provider versions; state in the private versioned GCS bucket; no secrets in state.
- Idempotent: a second apply with no change must show no diff.

## GKE invariants (changing any of these is a new comparison series — ask first)

Zonal GKE Standard, pinned version, fixed machine types, whole-number CPU requests = limits, static CPU manager policy,
compact placement, Dataplane V2, HPA and cluster autoscaling off, fixed replica counts, private nodes,
DNS-based control-plane endpoint, custom least-privilege node service account.

## Helm

- Own charts only (no Bitnami). Redis 8 official image pinned by digest (≥ 8.10.1), ACL user, default user disabled.
- Images by digest, not tag. Requests = limits, whole CPUs.
- Bid endpoint: internal passthrough NLB with `externalTrafficPolicy: Local`. Prediction: headless Service.
- Control and metadata endpoints: separate ports and Services, never exposed via a load balancer.
- Default-deny NetworkPolicy; allow only SSP→DSP, DSP→prediction, DSP→Redis, orchestrator→control/metadata.

## CI (GitHub Actions)

- Every action pinned to a commit SHA; never `pull_request_target`.
- Keyless Workload Identity Federation restricted by numeric `repository_owner_id` and `repository_id`; CI identity can only push images.
- Pipeline: build, test, golangci-lint, govulncheck, push to Artifact Registry.

## Local (Docker Compose)

Mirror the cluster topology functionally: DSP, prediction, Redis, SSP, Pub/Sub emulator, fake-gcs-server.
Results are always `non-reference`. Subscription expiration is not emulated — test that path on GKE only.

## Before finishing

- `terraform fmt -check && terraform validate`, `helm lint`, `helm template | kubeconform` if available.
- State in the PR whether the change affects the measured environment (and so starts a new comparison series).
