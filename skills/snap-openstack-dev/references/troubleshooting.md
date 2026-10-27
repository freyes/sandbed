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

If `blocked_reason` is set in `status`, a previous stage was interrupted and its remote work is
not confirmed stopped. Collect, then destroy the lab.

## Detached jobs

| Symptom | What it means and what to do |
|---|---|
| `job.state` is `lost` | The unit is gone and left no result. Run `sandbed dev collect`, then `sandbed dev destroy`, then create a new lab. |
| `interrupted` (or `failed` with "worker exceeded RuntimeMaxSec") and `job.service_result` is `timeout` | The per-command time cap was hit. Read the stderr tail and `sandbed dev job logs`, collect, then destroy and recreate the lab, and report the stage that was running. |
| `host check` fails `systemd-user` | Report the remediation verbatim: "enable a user manager: sudo loginctl enable-linger $USER, then export XDG_RUNTIME_DIR=/run/user/$(id -u) (needs systemd >= 240)". Stop. |
| `host check` fails `sudo` | Report the remediation verbatim: "background jobs run without a terminal, so cached 'sudo -v' credentials do not carry over: allow passwordless sudo (NOPASSWD) for this user". Stop. |
| `sandbed dev job status` exits 3 | The systemd user manager is unreachable and the job has no recorded result. Follow the remediation (`loginctl enable-linger $USER`, `systemctl --user status`), then check `sandbed dev host check`. |
