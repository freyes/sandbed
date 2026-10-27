"""The one built-in scenario: fresh bootstrap, configure, launch, reach the instance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .context import LabContext
from .errors import InputError, ScenarioError
from .provision import verify_identity
from .stages import GUEST_DIR, run_guest, stage
from .store import atomic_write_json

NAME = "bootstrap-and-launch"
VERSION = 1
BOOTSTRAP_TIMEOUT = 60 * 60
CONFIGURE_TIMEOUT = 20 * 60
LAUNCH_TIMEOUT = 15 * 60
LAUNCH_RE = re.compile(
    r"ssh -i (?P<key>\S+) (?P<user>[\w.-]+)@(?P<fip>\d{1,3}(?:\.\d{1,3}){3})"
)


@dataclass
class Outcome:
    """Filled in as steps pass, so a failed run still reports what it did establish."""

    assertions: dict[str, bool] = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


def parse_launch(output: str) -> tuple[str, str, str]:
    """(private key path, user, floating IP) from the line `sunbeam launch` prints."""
    match = LAUNCH_RE.search(output)
    if not match:
        raise ScenarioError("could not find the floating IP in `sunbeam launch` output")
    return match["key"], match["user"], match["fip"]


def parse_json(text: str):
    """First JSON document in `text`; the guest wrapper merges stderr into stdout."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[\[{]", text, re.M):
        try:
            return decoder.raw_decode(text[match.start():])[0]
        except json.JSONDecodeError:
            continue
    raise ScenarioError(f"no JSON in guest output: {text.strip()[:200]!r}")


class Steps:
    def __init__(self, ctx: LabContext, run_id: str, outcome: Outcome) -> None:
        self.ctx, self.run_id, self.outcome = ctx, run_id, outcome
        self.dir = f"{ctx.record.home}/{GUEST_DIR}"

    def sh(self, attempt, argv: list[str], timeout: float, name: str, *, check: bool = True):
        return run_guest(
            self.ctx.target(), attempt, argv, home=self.ctx.record.home, timeout=timeout,
            log_path=self.ctx.paths.run(self.run_id) / "commands" / f"{name}.log",
            check=check, tail_bytes=65536,
        )

    def openstack(self, attempt, args: list[str], name: str, *, check: bool = True):
        return self.sh(attempt, ["bash", f"{self.dir}/os.sh", *args], 300, name, check=check)

    def record(self, **evidence: object) -> None:
        self.outcome.evidence.update(evidence)
        atomic_write_json(
            self.ctx.paths.run(self.run_id) / "scenario-evidence.json", self.outcome.evidence
        )

    def step(self, name: str):
        return stage(self.ctx.log, self.run_id, f"scenario.{name}", layer=ScenarioError)


def run_scenario(ctx: LabContext, run_id: str, outcome: Outcome) -> None:
    rec, steps = ctx.record, Steps(ctx, run_id, outcome)
    out = outcome

    with steps.step("precondition") as a:
        if not rec.installed_artifact_id:
            raise InputError("no artifact is installed; run `sandbed dev deploy` first")
        # Bootstrap is the point of no return: after it started (and maybe failed or was
        # interrupted) this lab is not fresh. A failed *precondition* may be retried.
        if any(x.stage == "scenario.bootstrap" for x in ctx.log.attempts()):
            raise InputError("a scenario already ran in this lab; use a new lab")
        verify_identity(rec)
        steps.sh(a, ["test", "-c", "/dev/kvm"], 60, "precondition-kvm")
        out.assertions["kvm"] = True
        steps.record(artifact_id=rec.installed_artifact_id)

    with steps.step("bootstrap") as a:
        steps.sh(a, ["sunbeam", "cluster", "bootstrap", "--accept-defaults", "--role",
                     "control,compute,storage", "--manifest", f"{steps.dir}/manifest.yaml"],
                 BOOTSTRAP_TIMEOUT, "bootstrap")
        listing = steps.sh(a, ["sunbeam", "cluster", "list"], 300, "cluster-list").tail
        out.assertions["bootstrap"] = rec.vm_name in listing
        if not out.assertions["bootstrap"]:
            raise ScenarioError(f"{rec.vm_name} is not in `sunbeam cluster list`")

    with steps.step("configure") as a:
        steps.sh(a, ["sunbeam", "configure", "--accept-defaults", "--openrc",
                     f"{steps.dir}/openrc"], CONFIGURE_TIMEOUT, "configure")
        projects = parse_json(steps.openstack(a, ["project", "list", "-f", "json"],
                                              "project-list").tail)
        out.assertions["authenticated"] = bool(projects)
        if not projects:
            raise ScenarioError("authentication worked but no projects were listed")

    name = f"sbd-{run_id[:8]}"
    with steps.step("launch") as a:
        launched = steps.sh(a, ["sunbeam", "launch", "ubuntu", "--name", name], LAUNCH_TIMEOUT,
                            "launch").tail
        key, user, fip = parse_launch(launched)
        server = parse_json(steps.openstack(a, ["server", "show", name, "-f", "json"],
                                            "server-show").tail)
        image = parse_json(steps.openstack(a, ["image", "show", "ubuntu", "-f", "json"],
                                           "image-show").tail)
        steps.record(server_id=server["id"], server_name=name, floating_ip=fip, ssh_user=user,
                     image={k: image.get(k) for k in ("id", "name", "checksum", "os_hash_value")})
        out.assertions["server_active"] = server.get("status") == "ACTIVE"
        if not out.assertions["server_active"]:
            raise ScenarioError(f"server {name} is {server.get('status')}, not ACTIVE")

    with steps.step("guest-reachability") as a:
        out.assertions["guest_ssh"] = False
        steps.sh(a, ["bash", f"{steps.dir}/probe-guest.sh", key, fip, "900"],
                 LAUNCH_TIMEOUT + 60, "guest-probe")
        out.assertions["guest_ssh"] = True

    with steps.step("cleanup") as a:
        server_id = out.evidence["server_id"]
        steps.openstack(a, ["server", "delete", server_id, "--wait"], "server-delete")
        steps.openstack(a, ["floating", "ip", "delete", fip], "fip-delete")
        gone = steps.openstack(a, ["server", "show", server_id], "server-gone", check=False)
        out.assertions["cleanup"] = not gone.ok
        if gone.ok:
            raise ScenarioError("the scenario server still exists after cleanup")