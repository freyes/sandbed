# Troubleshooting by failed stage

Start with `report_path`, then the named files in `diagnostics_path` (`index.json` lists every
collector with its status, hash and errors; unavailable collectors are recorded, not hidden).

| `failed_stage` | Look at |
|---|---|
| `provision` | `commands/terraform-*.log` in the run directory; `host check` |
| `build.snapshot` | the `message`; rejected paths, submodules, symlinks outside the tree |
| `build.pack` | `commands/snapcraft-pack.log` |
| `build.inspect` | the `message`: unsupported snap version, base or architecture |
| `deploy.transfer` | checksum mismatch means the snap changed in transit |
| `deploy.install`, `deploy.prepare-node`, `deploy.connect`, `deploy.verify` | `stage-logs`, `snap-list`, `snap-connections` |
| `scenario.precondition` | KVM in the guest; the lab was not fresh |
| `scenario.bootstrap` | `stage-logs`, `clusterd-journal`, `sunbeam-inspect/` |
| `scenario.configure` | `stage-logs`, `openstack-networks` |
| `scenario.launch` | `openstack-servers`, `instance-console` |
| `scenario.guest-reachability` | `instance-console`, `openstack-servers`; the instance may be ACTIVE and unreachable |
| `scenario.cleanup` | the instance and its floating IP may remain; `destroy` removes the lab anyway |

A detached build keeps its progress output in `log_path` (`runs/<run>/detached.log`) and its
final envelope in `result_path` (`runs/<run>/result.json`); `status` shows the same envelope as
`last_run.result`. The `commands/` logs above are written for detached builds too.

If `blocked_reason` is set in `status`, a previous stage was interrupted and its remote work is
not confirmed stopped. Collect, then destroy the lab.
