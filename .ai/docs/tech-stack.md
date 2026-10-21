Backend – Go for every component on the measured path:
- go 1.27
- `github.com/kelindar/bitmap` (≥ v1.5.7)
- `github.com/redis/go-redis/v9`
- `cloud.google.com/go/pubsub`
- `cloud.google.com/go/storage`
- `prometheus/client_golang`

Database:
- Redis 8.10.x

CTR prediction service – Go with gRPC:
- go 1.27
- grpc-go
- protobuf

SSP simulator – Go on a dedicated GCE VM:
- go 1.27
- `github.com/HdrHistogram/hdrhistogram-go`

Infrastructure:
- Google Cloud Platform
- Terraform
- Helm

Infrastructure as code and local development – Docker Compose:
- Docker Compose for local functional checks, using the Pub/Sub emulator and fake-gcs-server in place of GCS; local results are marked `non-reference`. The emulator does not implement subscription expiration, so that path is tested only on GKE.

CI/CD:
- GitHub Actions
- golangci-lint
- govulncheck

CLI - scripts:
- taskfile
- `tenx` CLI in Go for scenario orchestration and reporting
