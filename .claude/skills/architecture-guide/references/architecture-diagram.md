## Architecture Diagram

```text
GCE VM, outside the cluster
  ┌─────────────────────────┐
  │ SSP simulator           │
  │ load + auction + HDR    │
  └────────────┬────────────┘
               │ HTTP/1.1 keep-alive, JSON (OpenRTB 2.6 subset)
  ┌────────────▼────────────┐
  │ L4 Network LB (GCP)     │
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐           ┌──────────────────────┐     ┌──────────────────┐
  │ DSP                     │   gRPC    │ CTR prediction API   │     │ Mock CTR table   │
  │ core + Scorer interface │◄─────────►│ + control endpoint   │◄────│ static, seeded   │
  └──┬────────▲───────▲─────┘           └──────────────────────┘     └──────────────────┘
     │        │       │ notify: new version (per-pod subscription)
  ┌──▼────┐   │   ┌───┴──────────┐      ┌───────────────────┐
  │ Redis │   │   │ Pub/Sub (GCP)│◄─────│ Inventory service │
  └───────┘   │   └──────────────┘      └─────────┬─────────┘
              │                                   │ upload snapshot
              │ load snapshot                     │
              │ (+ latest manifest at startup)    │
              │       ┌──────────────────────┐    │
              └───────│ GCS bucket (GCP)     │◄───┘
                      └──────────────────────┘
```