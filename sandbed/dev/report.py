"""The terminal result of a run: versioned JSON plus a short human report."""

from __future__ import annotations

from pathlib import Path

from .errors import EXIT_CODES, DevError
from .results import envelope
from .scenarios import NAME, VERSION, Outcome
from .store import atomic_write_json, write_private


def build_result(
    *,
    command: str,
    lab_id: str,
    label: str,
    run_id: str,
    error: DevError | None,
    outcome: Outcome,
    provenance: dict | None,
    collection: dict,
    run_dir: Path,
) -> dict:
    status = "succeeded" if error is None else error.status
    artifact = None
    if provenance:
        artifact = {
            "id": provenance["artifact_id"],
            "sha256": provenance["snap"]["sha256"],
            "snap_version": provenance["snap"]["version"],
            "adapter_qualified": provenance.get("adapter_qualified", False),
            "source_head": provenance["source"]["head"],
            "source_content_sha256": provenance["source"]["content_sha256"],
        }
    fields: dict = {
        "lab_id": lab_id,
        "label": label,
        "run_id": run_id,
        "artifact": artifact,
        "scenario": {"name": NAME, "version": VERSION},
        "assertions": outcome.assertions,
        "evidence": outcome.evidence,
        "collection_status": collection["status"],
        "preserved": True,
        "report_path": str(run_dir / "report.md"),
        "diagnostics_path": collection.get("diagnostics_path"),
    }
    if error is not None:
        fields.update(
            failure_category=error.category.value, failed_stage=error.stage, message=str(error),
            exit_code=130 if error.status == "interrupted" else EXIT_CODES[error.category],
        )
    return envelope(command, status, **fields)


def render_report(result: dict) -> str:
    lines = [f"# {result['command']} {result['status']}", ""]
    lines.append(f"- lab: `{result['label']}` (`{result['lab_id']}`), run `{result['run_id']}`")
    art = result.get("artifact") or {}
    if art:
        lines.append(
            f"- artifact: `{art['id']}` snap `{art['snap_version']}` sha256 `{art['sha256']}`"
        )
        lines.append(f"- source: `{art['source_head']}` content `{art['source_content_sha256']}`")
        lines.append(
            "- adapter qualified for this source commit: "
            + ("yes" if art["adapter_qualified"] else "NO (results are not release evidence)")
        )
    scenario = result["scenario"]
    lines.append(f"- scenario: `{scenario['name']}` v{scenario['version']}")
    if result["status"] != "succeeded":
        lines += ["", f"**Failed layer:** {result.get('failure_category')}",
                  f"**Failed stage:** {result.get('failed_stage')}",
                  f"**Message:** {result.get('message')}"]
    lines += ["", "## Assertions"]
    lines += [f"- {'PASS' if ok else 'FAIL'} {name}" for name, ok in result["assertions"].items()]
    lines += ["", f"Diagnostics: {result['collection_status']} (`{result['diagnostics_path']}`)",
              "Classification names the failing layer, not a proven product root cause."]
    return "\n".join(lines) + "\n"


def write_result(run_dir: Path, result: dict) -> None:
    atomic_write_json(run_dir / "result.json", result)
    write_private(run_dir / "report.md", render_report(result))
