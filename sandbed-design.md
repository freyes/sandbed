# Design — sandbed

- **Date:** 2026-09-29
- **Status:** Redesign (libvirt → LXD + microceph) — pending user review
- **Scope:** A turnkey harness that deploys Canonical OpenStack (Sunbeam) on **LXD** virtual
  machines, backed by a single-node **microceph** storage cluster on the host.

## 1. Goal

Build a project that provisions **LXD virtual machines** and then deploys **Canonical OpenStack
Sunbeam** onto them, driven by a single Python entry-point script. Two deployment providers must be
supported: **manual** and **MAAS**.

This is a redesign of the earlier libvirt-based plan: the bare-metal host now runs **LXD** (instead
of libvirt/qemu) to create the VMs, and a **single-node microceph snap** (instead of raw disks)
provides the LXD storage pool and the block devices passed to the VMs.

## 2. Requirements

- **Stack:** Terraform, LXD (QEMU/KVM-backed VMs), microceph, Python, Ubuntu **26.04** (no
  fallback).
- **Entry point:** a script used to drive the whole flow.
- **Terraform** installed from HashiCorp's apt repository.
- **Networking** provided by LXD. A virtual network named `mgmt` whose CIDR is a parameter
  defaulting to `192.168.200.0/24`.
- **Support `manual` and `maas` providers.**
- **Manual topology:** 3 VMs to host Sunbeam; default resources 4 vCPU / 8 GB RAM / 100 GB disk.
- **MAAS topology:** produce the VMs needed to implement the Canonical reference architecture
  (2024.1 "Example physical configuration").
- **Shared directory:** every VM gets a host directory mounted at `/shared`, source set via
  `--shared-dir=<DIR>`, served by LXD as a `disk` device (virtiofs/9p inside the VM).
- **Terraform LXD provider:** Canonical `terraform-lxd/lxd` talking to the local LXD socket.
- **Storage:** a single-node **microceph** cluster (snap) on the host, using a **file-backed loop
  OSD**, backs the LXD `ceph` storage pool (VM root disks) and exports RBD block devices into each
  VM as `/dev/sdb`.
- **Documentation:** a set of Markdown files under `docs/` with an index.
- **MAAS deployed and configured by the harness:** `maas-1` (maas + maas-test-db snaps) built by
  Terraform; MAAS configured by Terraform (Canonical `maas` provider): images, network/DHCP, machines,
  tags, LXD power driver.

## 3. Decisions log

| Decision | Choice |
|---|---|
| Scope boundary | Provision VMs (Terraform/LXD), deploy MAAS itself in an LXD VM, configure it with the Canonical `maas` Terraform provider, and drive Sunbeam (script) |
| Hypervisor | **LXD** (snap), VMs are QEMU/KVM-backed (`type = "virtual-machine"`), not containers |
| Terraform provider | `terraform-lxd/lxd` (Canonical), local unix socket remote |
| Host storage | **Single-node microceph** (snap), file-backed loop OSD; provides LXD `ceph` pool + RBD `/dev/sdb` to VMs |
| Guest storage | Sunbeam deploys its **own** microceph inside the VMs, using `/dev/sdb` as its OSD (nested Ceph) |
| Module structure | Shared `network/` + `manual/`, `maas/` (lxd) and `maas-config/` (maas provider) modules; `sandbed sunbeam manual\|maas` selects |
| MAAS VM set | `maas-1` + `sunbeam-client-1` (cloud-init) + 9 core nodes + observability/landscape variable counts (default 3, 0 to 16); all but the client are empty PXE VMs enlisted in MAAS |
| MAAS control | MAAS `lxd` power driver over HTTPS to `<generic gateway>:8443` with a Terraform-generated client certificate trusted by LXD; PXE enlist/commission off `generic` served by maas-1 |
| Host contract | Assume LXD + microceph present (or installed by script); script installs terraform + `openstack` snap |
| Base image | Ubuntu 26.04 cloud image, no fallback |
| Shared dir | `--shared-dir=<DIR>` → LXD `disk` device (virtiofs/9p) mounted at `/shared` |
| Hooks | User executables in `hooks/`; filename = timing (`.pre`/`.post` per phase, `pre-run`/`post-run`, `on-fail`) |
| Cloud-init templates | Per-VM resolution: `<node>.user-data.yaml.tpl` → `default.user-data.yaml.tpl` → embedded; `<node>.network-config.yaml.tpl` → `default.network-config.yaml.tpl` → (LXD default) |
| Testflinger | `--use-testflinger` allocates a remote node via `testflinger-cli reserve`, scp's the repo, and re-runs remotely |
| MAAS-side setup | Terraform `maas-config` root; images block until synced; admin password is a variable (default `changeme`) and an output |

## 4. Repository layout

```
sandbed/
  sandbed                  # entry point (console script registered via pyproject.toml)
  pyproject.toml            # python deps + [project.scripts] sandbed entry point
  README.md                  # short pointer to docs/index.md
  terraform/
    versions.tf              # terraform + terraform-lxd/lxd version pins
    network/                 # shared: mgmt/external/generic LXD nets + microceph-backed ceph pool + base image
      main.tf  variables.tf  outputs.tf
    manual/                  # 3 hyperconverged VMs (+ cloud-init)
      main.tf  variables.tf  outputs.tf  cloud-init/
    maas/                    # maas-1, client, PXE nodes, LXD HTTPS/trust (+ cloud-init, files/)
      main.tf  variables.tf  outputs.tf  cloud-init/  files/
    maas-config/             # MAAS configuration via the canonical/maas provider
      providers.tf  variables.tf  main.tf  network.tf  machines.tf
  manifests/                 # Jinja2 sunbeam deployment-manifest templates
    manual.yaml.j2  maas.yaml.j2
  hooks/                    # user hooks (filenames = timing); README.md tracked, hooks/* gitignored
    README.md
  templates/
    cloud-init/             # user cloud-init templates; *.yaml.tpl gitignored, README.md tracked
      README.md
  docs/                      # user documentation (see §11)
    index.md
    getting-started.md  usage.md  configuration.md  architecture.md
    manual-provider.md  maas-provider.md  shared-directory.md
    cloud-init-templating.md
    reference-architecture.md  troubleshooting.md
  tests/
    test_cli.py  test_config.py  test_hooks.py  test_install.py  test_maas.py
    test_manifest.py  test_preflight.py  test_smoke.py  test_sunbeam.py  test_terraform.py
  .gitignore
```

## 5. Terraform — provider & `network/` module

- `terraform-lxd/lxd` provider (`~> 3.0`), speaking to the **local LXD unix socket** via an explicit
  `remote { name = "local"; address = "unix://" }` block (v3 requires explicit remotes; single-host
  contract; no remote LXD).
- `network/` module (provider-agnostic shared pieces):
  - Creates the **`mgmt`** LXD managed bridge; CIDR variable default `192.168.200.0/24`. Used for
    cloud-init/SSH provisioning; doubles as Sunbeam's management network in manual mode.
  - Creates the **`ceph`** LXD storage pool backed by host microceph (cluster name, user, pool via
    `config`), so VM root disks and `/dev/sdb` live as RBD images.
  - Caches the Ubuntu 26.04 cloud image (qcow2) as an LXD VM image via `lxd_image` +
    `source_image` (v3 replaces `lxd_cached_image`).
  - Outputs: `mgmt` network id, ceph pool id, base image fingerprint.
- Each provider module creates its own networks: `manual/` creates **`external`** (default
  `172.16.2.0/24`, used by `sunbeam configure` for floating IPs); `maas/` creates **`generic`**
  (default `172.16.1.0/24`, LXD DHCP off: MAAS on `maas-1` serves DHCP/PXE) **and** `external`.

## 6. Terraform — `manual/` module

- 3 VMs `cloud-1/2/3`; defaults 4 vCPU / 8 GB / 100 GB (all variables).
- Each VM: `mgmt` NIC + a 2nd NIC on `external` (floating IPs) + `/dev/sdb` (RBD block volume from
  the host-microceph `ceph` pool) for Sunbeam's storage role.
- Root disk: RBD volume in the `ceph` pool.
- cloud-init: rendered from a template resolved per-VM (see §8a) — user (`hostname`/`user`/SSH keys/
  `NOPASSWD` sudo/`/shared` mount) with `user.user-data` + optional `user.network-config`.
- Outputs: IPs, SSH target, external-net info.

## 7. Terraform — `maas/` and `maas-config/` modules

`maas/` (lxd provider) builds the networks and VMs; `maas-config/` (Canonical `maas` provider)
configures MAAS from `maas/`'s local state. See `docs/maas-provider.md` for the user view.

### Topology

```
generic 172.16.1.0/24   LXD DHCP off; MAAS serves DHCP, PXE and DNS
  .1  host bridge / gateway          .2  maas-1 (static)        .3  sunbeam-client-1 (static)
  .20-.39 reserved <name>-internal-api   .40-.59 reserved <name>-public-api
  .100-.200 dynamic range (enlist / commission)
mgmt     192.168.200.0/24   sunbeam-client-1 eth0 (host access)
external 172.16.2.0/24      cloud-* eth1 (floating IPs, `neutron:physnet1`)
```

| VM | NICs | Provisioned by |
|----|------|----------------|
| `maas-1` | `generic` (static `.2`) | cloud-init, then `maas-bootstrap.sh`: `maas` snap (region + rack) and `maas-test-db` snap |
| `sunbeam-client-1` | `mgmt` + `generic` (static `.3`) | cloud-init; drives Sunbeam |
| `cloud-1/2/3` | `generic` + `external` | empty PXE VM, stopped until MAAS powers it on |
| `sunbeam-controller-1/2/3`, `juju-controller-1/2/3` | `generic` | empty PXE VM |
| `observability-N`, `landscape-N` (default 3 each, 0 to 16) | `generic` | empty PXE VM |

Every PXE VM has a secondary `/dev/sdb` RBD volume (only the cloud nodes use it). Their MACs are
fixed (`00:16:3e:5a:01:<n>` on `generic`, `00:16:3e:5a:02:<n>` on `external`) so MAAS can be told
about them before they ever run. `maas-1` and the client have no `/dev/sdb`. `--shared-dir` is
mounted on `maas-1` and the client only (MAAS installs the other nodes, so cloud-init never runs
there).

### Phases

1. **terraform** applies `network`, then `maas` (`terraform/maas/`): networks, `maas-1` (bootstrapped
   through `lxd_instance` `execs`), the client, the PXE VMs, `core.https_address` on the `generic`
   gateway and a generated client certificate that LXD trusts for MAAS power control.
2. **maas-setup** applies `terraform/maas-config/` with the Canonical `maas` provider. It reads
   `maas`'s local state, so no secret goes through the command line.
3. **sunbeam** runs on `sunbeam-client-1`.

### What `maas-setup` configures

| Area | Result |
|------|--------|
| Server | `default_osystem=ubuntu`, `default_distro_series=<--maas-deploy-series>`, `upstream_dns` |
| SSH | the deployment public key added to MAAS |
| Images | `ubuntu/<deploy series>` `amd64` selected; the commissioning series too if it differs. **Terraform blocks until every image is fully synced** (`--maas-image-sync-timeout`, default `60m`), then until the rack controller has them |
| Network | the discovered `generic` fabric, VLAN and subnet are imported and given space `--maas-space`, gateway `.1`, DNS `.2`; DHCP on, served by `maas-1` |
| Ranges | dynamic `.100-.200`; reserved `<name>-internal-api` and `<name>-public-api` |
| Machines | each PXE VM is added with the `lxd` power driver, commissioned and waited to `Ready` |
| Tags | see below; `ceph` storage tag on `/dev/sdb` of the cloud nodes; `neutron:physnet1` network tag, unconfigured, on their `external` NIC |

| Role | Machine tags |
|------|--------------|
| cloud | `openstack-<name>`, `control`, `compute`, `storage` |
| sunbeam-controller | `openstack-<name>`, `sunbeam` |
| juju-controller | `openstack-<name>`, `juju-controller` |
| observability, landscape | their own name only (no Sunbeam role) |

`<name>` is `--maas-name` (default `sandbed`).

## 8. Shared directory (`--shared-dir=<DIR>`)

- New optional flag `--shared-dir=<DIR>` on `sandbed` → `shared_dir` Terraform variable
  (default `""` = off). Applies to every VM in both modules.
- Per VM (LXD `disk` device, directory source → virtiofs/9p inside the VM):

```hcl
device {
  name = "shared"
  type = "disk"
  properties = {
    source = var.shared_dir
    path   = "/shared"
  }
}
```

- cloud-init (both modules):

```yaml
runcmd:
  - mkdir -p /shared
  - mount -t virtiofs shared /shared
  - echo "shared /shared virtiofs defaults 0 0" >> /etc/fstab
```

- Host prerequisite (preflight, only when `--shared-dir` set): host path exists; LXD ≥ 4.0 serves
  directory sources to VMs over virtiofs/9p (satisfied by the LXD snap on Ubuntu 26.04; no separate
  `virtiofsd`). Guest kernel ≥ 5.4 (satisfied by Ubuntu 26.04).
- Implementation note: exact terraform-provider-lxd `device`/`disk` field names to be pinned from
  the provider registry docs before coding.

## 8a. Cloud-init templating (`templates/cloud-init/`)

User-supplied cloud-init templates override the built-in ones, resolved per VM by Terraform at plan
time. Templates use Terraform template syntax. Template variables: `hostname`, `user`, `ssh_key`,
`shared_enabled`, `provider`.

**user-data resolution order (per VM):**
1. `templates/cloud-init/<node>.user-data.yaml.tpl` — per-node override (e.g. `cloud-1`,
   `sunbeam-controller-2`)
2. `templates/cloud-init/default.user-data.yaml.tpl` — user default
3. embedded `terraform/{manual,maas}/cloud-init/user-data.yaml.tpl` — final fallback

**network-config resolution order (per VM):**
1. `templates/cloud-init/<node>.network-config.yaml.tpl` — per-node override
2. `templates/cloud-init/default.network-config.yaml.tpl` — user default
3. (no fallback) — `user.network-config` is omitted so LXD's default (auto-DHCP) applies

Implemented via `fileexists()` + nested ternary; `sandbed` resolves the dir to an absolute path
(`--cloud-init-templates-dir`, default `templates/cloud-init`). When a user overrides `user-data`,
the `/shared` mount `runcmd` is no longer auto-injected — the user template owns it (see
`docs/cloud-init-templating.md`). In the maas module only `maas-1` and `sunbeam-client-1` consume
cloud-init (every other node is an empty PXE VM), so other `<node>` overrides are inert; their
network-config is static, so `default.network-config.yaml.tpl` is not applied to them.

## 9. Python entry point `sandbed`

CLI: `sandbed sunbeam manual|maas {up|plan|destroy|sunbeam} [flags]`. Gated, resumable phases:

1. **preflight** — LXD reachable and initialized; KVM accel check; microceph healthy, single-node,
   RBD-enabled; if `--shared-dir` set, verify host path exists.
2. **install tools** — `snap install lxd` + `lxd init --auto`; `snap install microceph` +
   `microceph cluster bootstrap` + create sparse file (`truncate -s <size>`) + `losetup` +
   `microceph disk add <loopdev> --wipe` + wire LXD↔Ceph (`ceph.conf` + keyring); **verify Ceph is
   functional** (`microceph.ceph -s` succeeds, ≥1 OSD reports `up` via `microceph.ceph osd stat`,
   and the RBD pool is listed by `microceph.rados lspools`) — this must pass **before** phase 3
   creates the `ceph` LXD storage pool; add HashiCorp apt repo + `apt install terraform`;
   `snap install openstack`. Skip if already present.
3. **terraform** — `init/plan/apply` for the selected module, passing all variables (`shared_dir`,
   `mgmt_cidr`, VM resources, image, obs/landscape counts, ceph pool params).
4. **maas setup (MAAS path only)** — `terraform apply` of `maas-config/` (see §7); blocks until
   images are synced and machines are `Ready`.
5. **sunbeam** —
   - *manual*: SSH `cloud-1` → `snap install openstack` → `prepare-node-script --bootstrap | bash`
     → `cluster bootstrap --role control,compute,storage` (manifest-driven, non-interactive)
     → add `cloud-2/3` (scale-out flow) → `configure` → print dashboard URL/openrc.
   - *maas*: SSH `sunbeam-client-1` → `snap install openstack` →
     `prepare-node-script --client | bash` → `deployment add maas <name> <token> <url>`
     → `deployment space map <space>` → `deployment validate` → `cluster bootstrap`
     → `cluster deploy` → `configure` → print dashboard URL.
6. **destroy** — sunbeam teardown (warning on ordering) → `terraform destroy` → optional
   microceph/LXD teardown (warning).

Non-interactivity: render a Sunbeam deployment manifest from `manifests/*.j2` and pass it to the
sunbeam commands.

**Hooks:** user executables in `hooks/` run at the phase boundaries, keyed by filename —
`pre-run`/`post-run` (whole action), `<phase>.pre`/`<phase>.post` (the six phases, plus `destroy`),
and `on-fail` (a failing phase, in place of its `.post`). A non-zero exit aborts the run. Hooks
receive `SDB_PROVIDER`, `SDB_ACTION`, `SDB_PHASE`, `SDB_HOOK`, and configured flags as
`SDB_CFG_<NAME>`; `on-fail` receives the failed phase's stdout+stderr on stdin. Disable with
`--no-hooks`; override the directory with `--hooks-dir` (default `hooks/`).

**SSH key:** unless `--ssh-key` is given, sandbed creates a dedicated password-less ed25519 key in the
working directory (`.sandbed_ed25519`, only if absent), authorizes its public half on every node
(cloud-init, MAAS, the Testflinger node) and ssh-es with `-i` to the client.

**Testflinger:** with `--use-testflinger`, the invocation is delegated to remote execution instead
of running locally — allocate a node (`testflinger-cli reserve`) or reuse a job, wait for its
`device_ip` and for `ssh` to be accepted (retry every 15 s, up to 5 min), `rsync` the repo (minus `.venv`, `__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.terraform`) to `ubuntu@<ip>:~/sandbed`, then run
`uv run sandbed <args>` (testflinger flags stripped). The host preparation and the remote run are
started detached under `nohup` on the node (`~/.sandbed-jobs/<name>.{log,pid,rc}`) and followed over
ssh; a dropped connection is re-established (every 15 s, up to 5 min) and following resumes from the
last complete line, and re-running attaches to a job that is still running. The allocated job id is persisted
to `$CWD/.sandbed.testflinger.job_id`; a later implicit reserve reuses it if it is live
(`waiting`/`provision`/`test`/`reserve`) and on the same queue, otherwise prints a warning
(job unknown to testflinger, no longer alive, or other queue), dismisses the stale id, and submits
fresh, overwriting the file.

## 10. Variables (defaults)

- `lxd_remote` — **not** configurable: local unix socket (single-host contract).
- `mgmt_cidr = 192.168.200.0/24`
- `ubuntu_image_url` (26.04 cloud image, qcow2) + `ubuntu_image_checksum`
- `shared_dir = ""`
- microceph: `microceph_osd_size` (default `50` GiB sparse file),
  `microceph_osd_img` (default `/var/snap/microceph/common/osd.img` — path to the file that backs
  the loop OSD, overridable), `ceph_cluster_name = ceph`, `ceph_user = admin`, `ceph_pool`
  (RBD pool name)
- manual: `vm_count = 3`, `vcpus = 4`, `memory = 8192`, `disk = 100`,
  `external_cidr = 172.16.2.0/24`
- maas: `obs_count = 3`, `landscape_count = 3`, `generic_cidr`, `external_cidr`, `maas_name = sandbed`,
  `maas_space = myspace`, `maas_admin_password = changeme`, `maas_channel = 3.6/stable`,
  `maas_vcpus/memory/disk = 4/8192/50`, `deploy_series = noble`, `image_sync_timeout = 60m`,
  `upstream_dns = 1.1.1.1`, `lxd_https_port = 8443`; `maas_url`/`maas_token` are derived, not inputs.
- sunbeam: `region = RegionOne`, `db_topology = single`, `project_network = 192.168.0.0/24`,
  external-net params, demo credentials
- hooks: `hooks_dir = hooks` (default), `no_hooks = false`
- cloud-init: `cloud_init_templates_dir = templates/cloud-init` (default; resolved to absolute path)
- testflinger: `use_testflinger = false`, `testflinger_series = resolute`, `testflinger_queue` (required),
  `testflinger_timeout = 6h` (humanized), `testflinger_job`, `testflinger_job_file`,
  `testflinger_key` (repeatable, required on allocate); job id persisted to
  `$CWD/.sandbed.testflinger.job_id` for reuse

Source: CLI flags / environment variables / `config.yaml`.

## 11. Documentation

Set of Markdown files under `docs/` with `docs/index.md` as the index (landing page + linked ToC).
`README.md` is a short pointer to `docs/index.md`. Files:

- `index.md` — landing + linked table of contents
- `getting-started.md` — host prerequisites, install, quickstart (manual + maas)
- `usage.md` — `sandbed` CLI reference (subcommands, flags, env vars)
- `configuration.md` — full variable/flag reference (LXD, microceph, sunbeam)
- `architecture.md` — repo layout, terraform modules, phases, data flow, host-storage model
- `manual-provider.md` — manual topology, networks, sunbeam flow
- `maas-provider.md` — MAAS topology, deployment and configuration, sunbeam flow
- `shared-directory.md` — `/shared` feature via LXD `disk` device (mechanics, requirements, usage)
- `hooks.md` — user hooks (naming/timing table, execution order, env + stdin contract, examples)
- `cloud-init-templating.md` — per-VM cloud-init template resolution, template vars, user-data vs
  network-config order, `/shared` ownership note, examples
- `testflinger.md` — `--use-testflinger` remote execution, flags, job-file templating, auth/keys
- `reference-architecture.md` — mapping to the Canonical reference arch (implemented vs. skipped)
- `troubleshooting.md` — common failures, log locations, teardown order

Docs are authored as implementation tasks alongside the code they describe.

## 12. Error handling & idempotency

- Local terraform state (remote backend optional). Re-runs safe (terraform idempotent).
- Per-phase gating; on failure print the exact failed command + log path; resumable via a small
  phase-state file.
- Missing prerequisites → fail fast with a remediation hint.
- `destroy` warns about sunbeam teardown ordering.

## 13. Testing & verification

- `terraform validate` + `terraform fmt -check` + `terraform plan` in CI (no apply). This validates
  the cloud-init template resolution (`fileexists()` + `merge`), the LXD provider v3 schema, and the
  `ceph` storage pool/vm resources.
- Python unit tests (mocked, `uv run pytest`) for: variable resolution, manifest rendering, phase
  gating, prerequisite checks (`preflight`), install idempotency + `verify_ceph` (OSD `up` / RBD pool
  present / abort on no OSD), terraform var mapping (incl. `cloud_init_templates_dir` absolutization),
  `maas-config` apply and MAAS output resolution, sunbeam manual/maas sequences, the `manual` pinning suite, and hooks
  (discovery, env + stdin contract, non-zero abort, `--no-hooks`).
- `sandbed ... plan` as dry-run (renders `terraform plan` without applying).
- Smoke (real host, manual): assert VMs reachable over `mgmt`; manual path asserts
  `sunbeam cluster list` (or equivalent cluster-status check); hooks fire in order; per-node
  cloud-init templates resolve.

## 14. Assumptions & risks

- **Ubuntu 26.04 only (accepted risk):** the 2024.1 how-tos target 24.04 LTS; the `openstack`,
  `lxd`, and `microceph` snaps may not yet declare 26.04 support. No fallback per decision.
- **Nested Ceph (accepted):** host microceph (single-node, loop-backed OSD) feeds RBD to guest
  Sunbeam microceph (3-node). Loop-backed OSD on a single host trades durability/performance for
  dev-box simplicity.
- **MAAS in one VM (accepted):** region and rack controllers share one LXD VM with the single-node,
  non-HA maas-test-db. The `maas` snap channel must support the 26.04 guest (default `3.6/stable`).
- **terraform-lxd maturity:** the Canonical `terraform-lxd/lxd` provider's support for `ceph`
  storage pools, VMs, and virtiofs `disk` devices must be validated against its registry docs before
  coding; 10–16 VMs need ample host RAM/CPU.

## 15. Out of scope

- MAAS high availability (several region/rack controllers, external PostgreSQL).
- Standalone OpenStack operations (upgrades, backup/restore, scaling) beyond the initial deploy.
- Any non-LXD target (bare metal, other hypervisors, LXD containers).
- Remote/SSH LXD targets; `sandbed` and Terraform run on the hypervisor itself.

## 16. Open questions

None outstanding — all decisions captured in §3.

## 17. Development labs (`sandbed dev`)

Additive workflow for testing a locally built snap-openstack. It shares no state, phase list or
hooks with `sandbed sunbeam`; see `docs/development-labs.md` for usage.

| Decision | Choice |
|---|---|
| Scope | manual provider, one hyperconverged VM, amd64, local execution, one scenario (`bootstrap-and-launch` v1) |
| Isolation | per-lab LXD project `sbd-<8 hex>`, bridge `sbd-<8 hex>-m` (bridges are not project-scoped), profile, volumes, Terraform state, SSH key (the image is shared, see below) |
| Shared host storage | RBD pool and LXD ceph pool `sandbed-dev` (microceph) or LXD `dir` pool `sandbed-dev` (dir), created by `sandbed dev host prepare`, never managed or removed by a lab |
| Shared host image | Ubuntu 26.04 VM image (x86_64), imported by `sandbed dev host prepare` into LXD's default project and used by every lab by fingerprint (lab projects set `features.images=false`; the Terraform LXD provider cannot copy it into a project over the unix socket, only via `core.https_address`); recorded in `host.json` as `image_remote`/`image_alias`/`image_fingerprint` |
| In-container storage | `dir` pool; outer admin passes nesting + KVM/vhost/tun devices. `--storage-backend auto|microceph|dir` (default `auto`), `--storage-source PATH`. `zfs.delegate` rejected: zvols aren't namespaced (lxc/incus#739); `ceph` driver needs krbd, not container-usable |
| Terraform | `terraform/dev-network` (project, bridge) and `terraform/dev-manual` (profile, volume, VM), copied per lab |
| State | `$XDG_STATE_HOME/sandbed/dev`; atomic JSON, per-lab flock, host registry lock, stage attempts with orphan reconciliation |
| Artifact | frozen git snapshot (snapcraft derives the version from git), `snapcraft pack`, provenance, `snap install --dangerous`, installed squashfs checksum compared with provenance |
| Guest access | Sunbeam local-access mode: floating IPs are reachable from the lab VM only; no external NIC or LXD bridge. The floating network (`172.16.2.0/24`) is pinned explicitly in the manifest |
| Failure policy | collect diagnostics, preserve the lab; a failed bootstrap needs a new lab |
| Public interface | `--json` envelope `schema_version` 1, exit codes 0/2/3/4/5/6/130, `sandbed dev capabilities` interface version 2 (storage backends `microceph`, `dir`) |