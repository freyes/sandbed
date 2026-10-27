from __future__ import annotations

from sandbed.dev.errors import ScenarioError
from sandbed.dev.report import build_result, render_report, write_result
from sandbed.dev.scenarios import Outcome
from sandbed.dev.store import read_json

PROV = {
    "artifact_id": "a-1", "snap": {"sha256": "S" * 64, "version": "2026.1-abc+dirty"},
    "adapter_qualified": False,
    "source": {"head": "H" * 40, "content_sha256": "C" * 64},
}


def result(error=None, assertions=None, tmp_path=None):
    return build_result(
        command="test", lab_id="L", label="bug-1", run_id="R", error=error,
        outcome=Outcome(assertions or {"server_active": True}), provenance=PROV,
        collection={"status": "partial", "diagnostics_path": "/d.tar.gz"}, run_dir=tmp_path,
    )


def test_failed_result_has_the_published_fields_and_exit_code(tmp_path) -> None:
    err = ScenarioError("ssh failed", stage="scenario.guest-reachability")
    r = result(err, {"server_active": True, "guest_ssh": False}, tmp_path)
    assert (r["status"], r["failure_category"], r["exit_code"]) == ("failed", "scenario", 5)
    assert r["failed_stage"] == "scenario.guest-reachability"
    assert r["artifact"]["sha256"] == "S" * 64 and r["artifact"]["source_head"] == "H" * 40
    assert r["scenario"] == {"name": "bootstrap-and-launch", "version": 1}
    assert r["assertions"] == {"server_active": True, "guest_ssh": False}
    assert (r["collection_status"], r["preserved"]) == ("partial", True)
    assert r["schema_version"] == 1


def test_success_result_has_no_failure_fields(tmp_path) -> None:
    r = result(None, None, tmp_path)
    assert r["status"] == "succeeded" and "failure_category" not in r and "exit_code" not in r


def test_report_names_the_artifact_and_every_assertion(tmp_path) -> None:
    text = render_report(result(ScenarioError("x", stage="s"), {"a": True, "b": False}, tmp_path))
    assert "S" * 64 in text and "H" * 40 in text
    assert "adapter qualified for this source commit: NO" in text
    assert "PASS a" in text and "FAIL b" in text and "not a proven product root cause" in text


def test_write_result_is_private_json_next_to_the_report(tmp_path) -> None:
    write_result(tmp_path, result(None, None, tmp_path))
    assert read_json(tmp_path / "result.json")["run_id"] == "R"
    assert (tmp_path / "report.md").read_text().startswith("# test succeeded")
    assert (tmp_path / "result.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "report.md").stat().st_mode & 0o777 == 0o600
