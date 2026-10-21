"""Versioned JSON envelope shared by every `sandbed dev` command."""

from __future__ import annotations

import json

import click

from .errors import DevError

SCHEMA_VERSION = 1


def envelope(command: str, status: str, **fields: object) -> dict:
    """Common keys first; `lab_id`, `label` and `run_id` are always present (maybe null)."""
    base: dict = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": status,
        "lab_id": None,
        "label": None,
        "run_id": None,
    }
    base.update(fields)
    return base


def error_envelope(command: str, err: DevError) -> dict:
    return envelope(
        command,
        err.status,
        failure_category=err.category.value,
        failed_stage=err.stage,
        message=str(err),
        evidence=list(err.evidence),
        **err.context,
    )


def emit(payload: dict, *, json_mode: bool, human: str = "") -> None:
    """JSON mode: exactly one object on stdout. Otherwise a human line on stdout (stderr for
    failures). Progress output never goes through here; it goes to stderr."""
    if json_mode:
        click.echo(json.dumps(payload, sort_keys=False))
        return
    failed = payload["status"] != "succeeded"
    click.echo(human or payload.get("message") or payload["status"], err=failed)
