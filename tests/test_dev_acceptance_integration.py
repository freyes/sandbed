"""Real-host acceptance tests for `sandbed dev`. Skipped unless SANDBED_DEV_INTEGRATION=1.

When enabled they refuse to run on an unready host and say exactly what is missing; they never
fall back to anything fake. Required: SANDBED_DEV_SOURCE = a snap-openstack checkout at the
commit being qualified.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SANDBED_DEV_INTEGRATION") != "1",
    reason="set SANDBED_DEV_INTEGRATION=1 to run real-host acceptance",
)
CLI = [sys.executable, "-c", "from sandbed.cli import cli; cli()"]
CANARY = "SANDBED-CANARY-9f3c"


def sbd(*args: str, timeout: int = 7200) -> tuple[int, dict]:
    proc = subprocess.run([*CLI, "dev", *args], capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"unparsed_stdout": proc.stdout, "stderr": proc.stderr[-2000:]}
    return proc.returncode, payload


@pytest.fixture(scope="session")
def host_ready() -> None:
    code, payload = sbd("host", "check", "--json", timeout=300)
    if code != 0:
        failed = [c for c in payload.get("checks", []) if not c["ok"]]
        pytest.fail(
            "host is not ready for real-host qualification: "
            + "; ".join(f"{c['name']} ({c['detail']}): {c['remediation']}" for c in failed)
        )


@pytest.fixture(scope="session")
def source() -> str:
    path = os.environ.get("SANDBED_DEV_SOURCE")
    if not path:
        pytest.fail("SANDBED_DEV_SOURCE must point at the snap-openstack checkout under test")
    return path


@pytest.fixture
def lab(host_ready):
    """A fresh disposable lab, destroyed afterwards even if the test failed."""
    label = f"it-{uuid.uuid4().hex[:8]}"
    code, payload = sbd("create", "--id", label, "--json")
    assert code == 0, payload
    try:
        yield label
    finally:
        sbd("destroy", "--id", label, "--json")


def built(lab: str, source: str) -> dict:
    code, built = sbd("build", "--id", lab, "--source", source, "--json")
    assert code == 0, built
    return built


def test_happy_path_identifies_the_exact_snap_and_scenario(lab, source) -> None:
    artifact = built(lab, source)
    code, deployed = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert code == 0, deployed
    assert deployed["installed_sha256"] == artifact["artifact"]["sha256"]
    code, result = sbd("test", "--id", lab, "--json")
    assert code == 0, result
    assert result["artifact"]["sha256"] == artifact["artifact"]["sha256"]
    assert result["scenario"] == {"name": "bootstrap-and-launch", "version": 1}
    assert all(result["assertions"].values())


def test_tampered_snap_is_rejected_before_installation(lab, source) -> None:
    artifact = built(lab, source)
    pattern = f".local/state/sandbed/dev/labs/*/artifacts/{artifact['artifact_id']}"
    snap = next(Path.home().glob(pattern))
    (snap / "openstack.snap").chmod(0o600)
    with open(snap / "openstack.snap", "ab") as fh:
        fh.write(b"tamper")
    code, out = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert code == 4 and "no longer matches" in out["message"]


def test_two_labs_are_isolated_and_destroy_is_repeatable(host_ready) -> None:
    labs = [f"it-iso-{i}-{p}" for i, p in enumerate((CANARY[-4:], CANARY[-8:-4]))]
    for label in labs:
        assert sbd("create", "--id", label, "--json")[0] == 0
    try:
        a, b = (sbd("status", "--id", label, "--json")[1] for label in labs)
        assert a["vm_address"] != b["vm_address"] and a["lab_id"] != b["lab_id"]
        assert sbd("destroy", "--id", labs[0], "--json")[0] == 0
        assert sbd("destroy", "--id", labs[0], "--json")[0] == 0  # repeatable
        assert sbd("host", "check", "--json")[0] == 0  # shared backend intact
        assert sbd("status", "--id", labs[1], "--json")[1]["provisioning_state"] == "ready"
    finally:
        for label in labs:
            sbd("destroy", "--id", label, "--json")


def test_exported_archive_never_contains_credentials(lab, source) -> None:
    artifact = built(lab, source)
    deploy_code, _ = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert deploy_code == 0, "deploy must succeed for the archive to be meaningful"
    test_code, result = sbd("test", "--id", lab, "--json")
    assert test_code == 0, "test must succeed to produce a diagnostics archive"
    with tarfile.open(result["diagnostics_path"]) as tar:
        members = tar.getmembers()
        assert members, "diagnostics archive must have at least one member"
        names = {m.name for m in members}
        assert "index.json" in names, "diagnostics archive must contain index.json"
        for member in members:
            data = tar.extractfile(member).read().decode(errors="replace")
            assert "OS_PASSWORD" not in data and "PRIVATE KEY" not in data, member.name
            assert CANARY not in data
