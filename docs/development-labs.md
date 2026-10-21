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

`prepare` runs `scripts/prep-host.sh` (LXD, MicroCeph, uv, Terraform, loop OSDs) and then creates
the shared RBD pool and LXD pool `sandbed-dev`. Those belong to the host: lab teardown never
removes them. Also needed: `snapcraft`, `squashfs-tools`, KVM with nested virtualization, and
free disk (300 GiB is the provisional threshold).

Provisional sizing per lab: 8 vCPU, 32 GiB RAM, 100 GiB root, 50 GiB storage disk. Calibrate
before relying on it.

## Commands

| Command | What it does |
|---|---|
| `dev capabilities` | interface version, commands, profiles, scenarios, sandbed build |
| `dev create --id L` | project, network, VM; waits for SSH, cloud-init, `/dev/kvm`, `/dev/sdb` |
| `dev build --id L --source DIR [--include F]` | frozen git snapshot, `snapcraft pack`, artifact + provenance |
| `dev deploy --id L --artifact A` | copy, verify checksum, install `--dangerous`, prepare node, connect plugs, verify |
| `dev test --id L [--scenario S]` | bootstrap, configure, launch, guest SSH probe, cleanup, then collect |
| `dev status --id L` | lab state and stage attempts |
| `dev collect --id L --run R` | another diagnostics attempt |
| `dev destroy --id L` | remove lab resources, keep results |

All accept `--json` (one object on stdout, progress on stderr).

## Exit codes

0 succeeded; 2 invalid input, incompatible state or unsupported profile; 3 host or provisioning
failure; 4 build or deployment failure; 5 scenario assertion failure; 6 internal failure or an
incomplete `collect`; 130 interrupted.

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
```

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
