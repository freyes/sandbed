# sandbed

A turnkey harness that provisions **LXD** virtual machines (backed by a single-node **microceph**
storage cluster) and deploys Canonical OpenStack **Sunbeam** onto them.

## Table of contents

- [Getting started](getting-started.md) — prerequisites, install, quickstart
- [Usage](usage.md) — `sandbed` CLI reference
- [Configuration](configuration.md) — full variable/flag reference
- [Architecture](architecture.md) — repo layout, Terraform modules, phases, host-storage model
- [Manual provider](manual-provider.md) — manual topology + sunbeam flow
- [MAAS provider](maas-provider.md) — MAAS topology + automation + sunbeam flow
- [Shared directory](shared-directory.md) — the `/shared` feature
- [Hooks](hooks.md) — user hook runners
- [Cloud-init templating](cloud-init-templating.md) — per-VM cloud-init template overrides
- [Testflinger](testflinger.md) — run on a Testflinger-allocated remote node
- [Reference architecture](reference-architecture.md) — mapping to the Canonical reference arch
- [Troubleshooting](troubleshooting.md) — common failures and teardown order