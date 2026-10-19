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
- **MAAS setup fully automated** by the script (commissioning, tags, ranges, spaces, LXD power
  driver).

## 3. Decisions log

| Decision | Choice |
|---|---|
| Scope boundary | Provision VMs (Terraform/LXD) **and** drive Sunbeam (script); MAAS infrastructure assumed running externally |
| Hypervisor | **LXD** (snap), VMs are QEMU/KVM-backed (`type = "virtual-machine"`), not containers |
| Terraform provider | `terraform-lxd/lxd` (Canonical), local unix socket remote |
| Host storage | **Single-node microceph** (snap), file-backed loop OSD; provides LXD `ceph` pool + RBD `/dev/sdb` to VMs |
| Guest storage | Sunbeam deploys its **own** microceph inside the VMs, using `/dev/sdb` as its OSD (nested Ceph) |
| Module structure | Shared `network/` module + `manual/` and `maas/` modules; `sandbed sunbeam manual\|maas` selects |
| MAAS VM set | Core HA (10 VMs) + `observability`/`landscape` as variable counts (default 3, settable 0/1) |
| MAAS control | MAAS `lxd` power driver over HTTPS (`power_type=lxd`); PXE enlist/commission off `generic` |
| Host contract | Assume LXD + microceph present (or installed by script); script installs terraform + `openstack` snap |
| Base image | Ubuntu 26.04 cloud image, no fallback |
| Shared dir | `--shared-dir=<DIR>` → LXD `disk` device (virtiofs/9p) mounted at `/shared` |
| Hooks | User executables in `hooks/`; filename = timing (`.pre`/`.post` per phase, `pre-run`/`post-run`, `on-fail`) |
| Cloud-init templates | Per-VM resolution: `<node>.user-data.yaml.tpl` → `default.user-data.yaml.tpl` → embedded; `<node>.network-config.yaml.tpl` → `default.network-config.yaml.tpl` → (LXD default) |
| Testflinger | `--use-testflinger` allocates a remote node via `testflinger-cli reserve`, scp's the repo, and re-runs remotely |
| MAAS-side setup | Script fully automates it via MAAS API/CLI |

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
    maas/                    # core HA VMs + optional obs/landscape (+ cloud-init)
      main.tf  variables.tf  outputs.tf  cloud-init/
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
  (default `172.16.1.0/24`, parent uplink `bridge.external_interfaces` so external MAAS can
  PXE-enlist) **and** `external`.

## 6. Terraform — `manual/` module

- 3 VMs `cloud-1/2/3`; defaults 4 vCPU / 8 GB / 100 GB (all variables).
- Each VM: `mgmt` NIC + a 2nd NIC on `external` (floating IPs) + `/dev/sdb` (RBD block volume from
  the host-microceph `ceph` pool) for Sunbeam's storage role.
- Root disk: RBD volume in the `ceph` pool.
- cloud-init: rendered from a template resolved per-VM (see §8a) — user (`hostname`/`user`/SSH keys/
  `NOPASSWD` sudo/`/shared` mount) with `user.user-data` + optional `user.network-config`.
- Outputs: IPs, SSH target, external-net info.

## 7. Terraform — `maas/` module

- LXD networks: `generic` `172.16.1.0/24` (bridges to external MAAS PXE space), `external`
  `172.16.2.0/24`, plus `mgmt`.
- Core HA VMs (10): `cloud-1/2/3` (2 NICs generic+external, AZ1/2/3),
  `sunbeam-controller-1/2/3`, `juju-controller-1/2/3`, `sunbeam-client-1`.
- Optional via variable counts (default 3, settable 0/1): `observability-1/2/3`, `landscape-1/2/3`.
- **Every VM** (core HA **and** optional obs/landscape) receives a secondary **`/dev/sdb`** RBD block
  volume from the host-microceph `ceph` pool, in addition to its root disk. `/dev/sdb` backs
  Sunbeam's storage role on the nodes that carry it and is left unused on the rest.
- cloud-init sets hostnames/SSH/`/shared` (rendered from per-VM templates, see §8a); `cloud-*` left
  minimal so MAAS redeploys them (PXE off `generic`, powered via MAAS `lxd` power driver).
- Outputs: MACs/IPs, AZs, NIC mapping, LXD instance names (consumed by sandbed + MAAS
  automation).

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
`docs/cloud-init-templating.md`). MAAS `cloud-*` nodes are empty PXE machines and consume no
cloud-init, so their `<node>` overrides are inert.

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
   `snap install openstack`; install MAAS CLI / `python-libmaas` (MAAS path). Skip if already
   present.
3. **terraform** — `init/plan/apply` for the selected module, passing all variables (`shared_dir`,
   `mgmt_cidr`, VM resources, image, obs/landscape counts, ceph pool params).
4. **maas setup (MAAS path only)** — via MAAS API/CLI (`maas_url`, `maas_token`): configure LXD
   HTTPS (`core.https_address`) + trust token; set machine power `power_type=lxd` (`power_address`,
   project, instance name); enlist + commission the VMs (PXE off `generic`); create reserved IP
   ranges (`<name>-internal-api`, `<name>-public-api`); assign machine tags (`openstack-<name>`,
   `control`/`compute`/`storage`, `sunbeam`, `juju-controller`); set network tag `neutron:physnet1`
   + `Unconfigured` subnet on the external NIC; set `ceph` storage tag on `/dev/sdb`.
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

**Testflinger:** with `--use-testflinger`, the invocation is delegated to remote execution instead
of running locally — allocate a node (`testflinger-cli reserve`) or reuse a job, wait for its
`device_ip`, `scp` the repo to `ubuntu@<ip>:~/sandbed`, then run
`uv run sandbed <args>` (testflinger flags stripped). The allocated job id is persisted
to `$CWD/.sandbed.testflinger.job_id`; a later implicit reserve reuses it if it is live
(`waiting`/`provision`/`test`/`reserve`) and on the same queue, otherwise submits fresh and
overwrites the file.

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
- maas: `obs_count = 3`, `landscape_count = 3`, `generic_cidr = 172.16.1.0/24`,
  `external_cidr = 172.16.2.0/24`, `maas_name`, `maas_url`, `maas_token` (required),
  `maas_space = myspace`, `internal_api_range`, `public_api_range`, `floating_ip_range`,
  `lxd_https_port = 8443`
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
- `maas-provider.md` — MAAS topology, LXD power driver + MAAS automation, sunbeam flow
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
  MAAS-automation call sequence (`power_type=lxd` params), sunbeam manual/maas sequences, and hooks
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
- **MAAS infrastructure external:** MAAS region/rack controllers assumed running; the script
  automates machine enlistment/commissioning, the LXD power driver, and all MAAS configuration, not
  the MAAS install. Requires MAAS ≥ 3.x (`lxd` power driver support) + LXD HTTPS trust.
- **terraform-lxd maturity:** the Canonical `terraform-lxd/lxd` provider's support for `ceph`
  storage pools, VMs, and virtiofs `disk` devices must be validated against its registry docs before
  coding; 10–16 VMs need ample host RAM/CPU.

## 15. Out of scope

- Installing/operating MAAS region/rack controllers themselves.
- Standalone OpenStack operations (upgrades, backup/restore, scaling) beyond the initial deploy.
- Any non-LXD target (bare metal, other hypervisors, LXD containers).
- Remote/SSH LXD targets; `sandbed` and Terraform run on the hypervisor itself.

## 16. Open questions

None outstanding — all decisions captured in §3.