# Development labs (`sandbed dev`)

Test a local snap-openstack change in an isolated VM and get machine-readable evidence of what
was installed and what ran. Additive: `sandbed sunbeam manual|maas` is unchanged and does not
share state with this workflow.

Scope of milestone 1: manual provider, one hyperconverged VM (control, compute, storage), amd64,
local execution on a dedicated Ubuntu host, one built-in scenario. Not included: MAAS or
Testflinger execution, multi-node, upgrades, `snap try`, custom scenarios, parallel agents,
automatic expiry. `--use-testflinger` is rejected by `sandbed dev`.

`sandbed dev` needs a source checkout of sandbed (it copies `terraform/dev-*`).

## Host

```bash
sandbed dev host check --json     # read-only
sandbed dev host prepare          # explicit, idempotent, never wipes a healthy backend
```

`prepare` provisions the shared LXD storage pool `sandbed-dev` that every lab draws from. That
pool belongs to the host: lab teardown never removes it. Two storage backends are supported,
selected by `--storage-backend auto|microceph|dir` (default `auto`):

- **microceph** (the default on bare metal): `prepare` runs `scripts/prep-host.sh`, which installs
  LXD, MicroCeph, uv and Terraform and adds loop-backed OSDs, then creates the shared RBD pool and
  the LXD `ceph` pool `sandbed-dev`. This is today's behaviour on a dedicated Ubuntu host.
- **dir** (auto-selected inside an LXD container): `prepare` runs `prep-host.sh` with
  `SANDBED_PREP_MICROCEPH=0` so MicroCeph is skipped, then creates an LXD `dir` pool named
  `sandbed-dev`, optionally rooted at `--storage-source PATH`. The source directory must be empty
  (LXD requires this for `lxc storage create ... dir source=...`).

`auto` resolves the backend in this order: if a `sandbed-dev` pool already exists, its driver wins
(`ceph` maps to microceph, `dir` maps to dir); otherwise, if `systemd-detect-virt --container`
reports a container, `dir` is chosen; otherwise `microceph`. An existing `sandbed-dev` pool is
never replaced: if the resolved backend's driver disagrees with the existing pool's driver,
`prepare` fails with a clear error rather than wiping the pool.

`SANDBED_PREP_MICROCEPH=0` is the env var `prep-host.sh` reads to skip the MicroCeph snap install,
the bootstrap and the loop OSD setup. `prepare` sets it automatically for the `dir` backend; you
can also export it yourself when invoking the script directly.

`host check` also reports two checks that detached jobs depend on. `systemd-user` passes when
`systemd-run --version` reports systemd 240 or newer and `systemctl --user show-environment`
reaches a user manager. `sudo` runs `sudo -n true` under `setsid -w` (no controlling terminal), so
it only passes with passwordless sudo (`NOPASSWD`). Every `sandbed dev` command that runs as a job
(see [Detached jobs](#detached-jobs)) has no terminal: `create` and `host prepare` need
`NOPASSWD`, and credentials cached with `sudo -v` in your shell do not carry over.

Also needed regardless of backend: `snapcraft`, `squashfs-tools`, KVM with nested virtualization,
and free disk (50 GiB is the provisional threshold).

Beyond the storage backend, `prepare` imports the pinned Ubuntu cloud image — `ubuntu` remote,
alias `26.04`, virtual-machine, x86_64 — into LXD's default project, so `create` copies it locally
instead of downloading it over the network. Re-running `prepare` is a no-op once the image is
present. The staged image is recorded in `host.json` (`image_remote`, `image_alias`,
`image_fingerprint`), and `host check` reports its presence as the `image` check.

Provisional sizing per lab: 8 vCPU, 32 GiB RAM, 100 GiB root, 50 GiB storage disk. Calibrate
before relying on it.

### Running inside an LXD container

`sandbed dev` can run inside an LXD container on a shared hypervisor, where loop devices and the
kernel RBD module are unavailable. The `dir` backend handles this case. The outer container needs
nesting enabled and the KVM, vhost and TUN device nodes passed through. From the host:

```bash
lxc config set <c> security.nesting=true
lxc config device add <c> kvm unix-char source=/dev/kvm
lxc config device add <c> vhost-net unix-char source=/dev/vhost-net
lxc config device add <c> vhost-vsock unix-char source=/dev/vhost-vsock
lxc config device add <c> tun unix-char source=/dev/net/tun
# optional, recommended: dedicated outer volume for the nested pool
lxc storage volume create <outer-pool> <c>-sandbed-dev size=400GiB
lxc config device add <c> sandbed-dev disk pool=<outer-pool> source=<c>-sandbed-dev path=/srv/sandbed-dev
# inside the container
sandbed dev host prepare --storage-source /srv/sandbed-dev
```

Passing `/dev/kvm`, `/dev/vhost-net`, `/dev/vhost-vsock` and `/dev/net/tun` weakens container
isolation. These devices aren't namespaced: the container shares the host's KVM, vhost and TUN
subsystems with everything else running there. That's the outer admin's call to make, not
sandbed's. `host check --json` reports `in_container: true` and lists any missing device under
`vm-devices` with the exact `lxc config device add` remediation. LXD detects VM support only when
its daemon starts: after passing the devices through, run `sudo systemctl reload snap.lxd.daemon`
(running instances keep running), otherwise `lxd-vm` stays failed. Before creating a `dir` pool,
`prepare` checks the free space at `--storage-source` (or LXD's own directory when none is given)
and stops without changing anything if it is below the threshold.

#### Why not ZFS delegation or loop devices

`zfs.delegate` (LXD 5.17+, ZFS 2.2+) delegates ZFS datasets to a container, which works for
nested containers. It doesn't work for nested VMs: zvols (block devices) aren't namespaced, so the
container can't own them and nested VMs fail with "Could not locate a zvol" (lxc/incus#739).
`zfs.delegate` is therefore not the mechanism sandbed uses for in-container storage.

LXD's `ceph` driver maps RBD images through krbd, the kernel RBD module, which isn't usable inside
a container. So even with loop devices passed through, a MicroCeph-backed LXD pool can't serve VMs
in a container.

The `dir` driver stores VM root disks and `content_type=block` volumes as raw files that qemu
opens directly. No kernel block subsystem is involved, so it works inside a container. That's why
`dir` is the in-container backend.

Loop-device passthrough is possible but fragile: loop devices are global kernel resources, not
namespaced, so two containers competing for the same minor number corrupt each other. It isn't
worth the fragility when `dir` already works.

## Commands

| Command | What it does |
|---|---|
| `dev capabilities` | interface version, commands, profiles, scenarios, sandbed build |
| `dev create --id L` | project, network, VM; waits for SSH, cloud-init, `/dev/kvm`, `/dev/sdb`. Fails fast (exit 3) if the pinned image is not staged — run `sandbed dev host prepare` first |
| `dev build --id L --source DIR [--include F]` | frozen git snapshot, `snapcraft pack`, artifact + provenance |
| `dev deploy --id L --artifact A` | copy, verify checksum, install `--dangerous`, prepare node, connect plugs, verify |
| `dev test --id L [--scenario S]` | bootstrap, configure, launch, guest SSH probe, cleanup, then collect |
| `dev status --id L` | lab state and stage attempts |
| `dev collect --id L --run R` | another diagnostics attempt |
| `dev destroy --id L` | remove lab resources, keep results |

All accept `--json` (one object on stdout, progress on stderr).

`host prepare`, `create`, `build`, `deploy`, `test`, `collect` and `destroy` also accept
`--detach`: start the command as a background systemd job, print a launch envelope and return
right away. See [Detached jobs](#detached-jobs).

| Command | What it does |
|---|---|
| `dev job status --job J` | state, progress and stderr tail of one job |
| `dev job logs --job J` | read a job log from a byte offset |
| `dev job wait --job J` | wait for a job to finish |
| `dev job cancel --job J` | stop a running job |
| `dev job list` | recent jobs, newest first |

## Exit codes

0 succeeded; 2 invalid input, incompatible state or unsupported profile; 3 host or provisioning
failure; 4 build or deployment failure; 5 scenario assertion failure; 6 internal failure or an
incomplete `collect`; 130 interrupted. A `--detach` launch exits 0 with `status: running`, whatever
the job later does. No exit code was added for jobs.

## State

`$XDG_STATE_HOME/sandbed/dev` (default `~/.local/state/sandbed/dev`), directories `0700`, files
`0600`:

```text
registry.json            labs and reserved CIDRs (host-wide, locked)
host.json                what `host prepare` configured
labs/<uuid>/lab.json     immutable configuration + observed facts
labs/<uuid>/attempts.json  every stage attempt
labs/<uuid>/ssh/         per-lab key and known_hosts
labs/<uuid>/terraform/   per-lab copies of dev-network and dev-manual with their state
labs/<uuid>/artifacts/<id>/{openstack.snap,provenance.json}
labs/<uuid>/runs/<run>/{result.json,report.md,diagnostics*/,diagnostics*.tar.gz,commands/}
labs/<uuid>/private/     unredacted collection output; never exported
jobs/<job_id>/{job.json,launched.json,started.json,env,result.json,exit.json,stdout.log,stderr.log}
```

## Detached jobs

Long commands run as transient `systemd-run --user` units, so they survive a closed terminal or a
dropped SSH session. With `--detach` the command returns at once and you follow the job; without
it the command blocks as before. Interface version 3 (`dev capabilities`) introduced this.

### Requirements

A reachable systemd `--user` manager and systemd 240 or newer (`host check` reports it as
`systemd-user`, with the linger state in the detail). Without a login session the manager needs
linger, and your shell needs `XDG_RUNTIME_DIR`:

```bash
sudo loginctl enable-linger $USER
export XDG_RUNTIME_DIR=/run/user/$(id -u)
```

Without linger a job still runs, but it ends when your last login session does. The launch
envelope reports `linger` (`true`, `false` or `null` when unknown). The python interpreter path
and the job directory must not contain whitespace or any of `" ' \ $ % ;`. sandbed refuses to
launch rather than escape them; set `XDG_STATE_HOME` to a plain path if yours has one. Passwordless
sudo is needed for `create` and `host prepare` (see [Host](#host)).

There is no opt-out and no fallback for hosts without systemd `--user`: those commands fail with
exit 3 and a remediation.

### Launching

```bash
sandbed dev build --id L --source DIR --detach --json
```

The launch envelope has `status: running`, exit code 0 and these fields: `job_id`, `unit`
(`sandbed-dev-<job_id>`), `job_path`, `stdout_path`, `stderr_path`, `linger` and `next` (the
`sandbed dev job wait` command to run). Input is validated and the host gate runs first, so a bad
label or a missing `systemd-user` or `sudo` check fails before a job exists. A lab that already
has an active job refuses a second one (exit 2, the error names the `job_id`).

The environment reaches the worker through a `0600` `env` file in the job directory, filtered by
an allowlist (`PATH`, `HOME`, `USER`, locale, `XDG_*_HOME`, proxy and CA variables, and the
`SANDBED_`, `TF_`, `LXD_` and `SNAPCRAFT_` prefixes). It is never passed on the command line.

### Following a job

All take `--job J` and `--json`. They are lock-free: they take no lab lock and write nothing.

| Command | Options |
|---|---|
| `sandbed dev job status` | none |
| `sandbed dev job logs` | `--stream stderr\|stdout` (default `stderr`), `--offset N` (default 0), `--max-bytes N` (default 65536, capped at 1 MiB); returns `next_offset`, `size`, `eof`, `finished` |
| `sandbed dev job wait` | `--timeout SEC` (default 300, capped at 3600); returns `timed_out` |
| `sandbed dev job cancel` | none; returns `cancelled` and, when already finished, `reason` |
| `sandbed dev job list` | `--active`, `--lab L`, `--limit N` (default 20) |

These exit 0 whenever the inspection itself worked, even if the job failed or timed out. Read the
outcome from the envelope: `job.state` (`starting`, `running`, `succeeded`, `failed`,
`interrupted`, `lost`), `job.exit_code`, `job.service_result` (what systemd recorded) and
`job.result` (the command's own envelope, with its own `status` and failure category).
`job.progress` carries `current_stage`, the last attempts and a stderr tail while the job runs.

### Blocking runs use the same unit

Without `--detach` the command still starts a transient unit and waits for it. Output and exit
codes match the old in-process run. The job's `stdout.log` and `stderr.log` are tailed to your
stderr while it runs, and the envelope is printed once at the end. Ctrl+C or SIGTERM stops the
unit and exits 130. If the blocking parent is SIGKILLed, the unit keeps running: find it with
`sandbed dev job list --active` and stop it with `sandbed dev job cancel`.

### Time limits

Each unit has a `RuntimeMaxSec` backstop. It is `ceil(1.25 x` the sum of the command's enumerated
stage deadlines`) + 120` seconds. Blocking runs are bounded by it too, which they were not before.
When it fires, systemd sends SIGTERM: a responsive worker publishes an `interrupted` result
(exit 130); a worker that does not stop in time is killed and the job ends `failed` ("worker
exceeded RuntimeMaxSec", exit 6). Either way `job.service_result` is `timeout`. Treat both as "the
per-command time cap was hit": collect, then destroy and recreate the lab.

| Command | RuntimeMaxSec |
|---|---|
| `host prepare` | 13620 s (3 h of enumerated budget, plus margin) |
| `create` | 7395 s |
| `build` | 4620 s |
| `deploy` | 9308 s |
| `test` | 14370 s |
| `collect` | 2895 s |
| `destroy` | 4695 s |

### Cancelling

The unit uses `KillMode=mixed` and `TimeoutStopSec=60s`. A cancel sends SIGTERM to the worker
only, which stops its own child process groups once, so Terraform sees a single interrupt and
exits cleanly. Anything left over gets SIGKILL after the stop timeout. This is the same contract
as [Interruption](#interruption): the job ends `interrupted` (exit 130), and remote guest work
may still be running.

Cancelling `host prepare` may leave `prep-host.sh` running. It runs as root through sudo, and
neither the worker nor the user manager can signal root processes. Check with `ps` and stop it
yourself.

### The job directory

`jobs/<job_id>/` is private (`0700`) and holds, each written by one process:

- `job.json`: command, parameters, lab, unit name, creation time. Written once.
- `launched.json`: written after `systemd-run` accepted the unit.
- `started.json`: written by the worker when it begins.
- `env`: the `0600` environment file.
- `result.json`: the final envelope, human line and exit code. It is persisted because the unit
  runs with `--collect`, which unloads a failed unit and its status with it.
- `exit.json`: `service_result` and exit details recorded by the unit's `ExecStopPost` finalizer.
  If the worker never published a result (killed, timed out, OOM), the finalizer writes a failed
  one.
- `stdout.log`, `stderr.log`: the unit's output.

Jobs are never pruned automatically. Delete old directories under `jobs/` yourself.

`dev status --id L` on a lab with a running job does not wait for the lab lock: it returns the
last recorded state with `busy: true`.

## Isolation

Each lab has its own LXD project (`sbd-<8 hex>`), a management bridge `sbd-<8 hex>-m`, a
profile, an image copy, project-scoped volumes, its own Terraform state and SSH key. LXD bridge
networks cannot be project-scoped, so the network is global and told apart by name. CIDRs come
from `10.213.0.0/16` and are checked against host routes and LXD networks.

Launched instances use Sunbeam's local-access mode: floating IPs are reachable from the lab VM
only, so no second NIC, external LXD bridge or host route is needed. The OpenStack floating
network itself (`172.16.2.0/24`, pinned in the manifest and recorded in `lab.json` as
`floating_cidr`) exists only inside the VM; it must not overlap the lab CIDR.

## Qualification status

An adapter lists the snap-openstack commits it was qualified against on a real host
(`qualified_heads`). Until a commit is listed, `build` still works but results say
`adapter_qualified: false` and the report marks them "not release evidence".

## Interruption

Ctrl+C stops the local process group and marks the attempt `interrupted`. That says nothing about
guest work: guest commands run under `run-stage.sh`, which leaves `pid`/`rc` files, and the next
command asks the guest what became of them. While remote work is running or unknown the lab
refuses mutation; `status`, `collect` and `destroy` still work. A failed or interrupted bootstrap
is not repaired: collect, destroy, create a new lab.

## Evidence and secrets

Exports contain only allowlisted collector output, redacted, size-capped and indexed with
hashes. SSH keys, OpenRC files, tokens and Terraform state are never exported; members of the
`sunbeam inspect` archive are filtered and each omission is recorded. A passing run proves the
named snap passed the named scenario once; external charms, images and packages are not pinned,
and their observed versions are recorded.

## Agent skill

`skills/snap-openstack-dev/` ships with the repository. Link or copy it into your agent's skill
directory (agents do not discover repository folders on their own), for example
`ln -s "$PWD/skills/snap-openstack-dev" ~/.config/opencode/skills/snap-openstack-dev`. The wheel
contains only the `sandbed` package, so the skill is delivered with source checkouts and release
source archives.
