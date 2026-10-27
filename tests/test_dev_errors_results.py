from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from sandbed.dev.errors import (
    EXIT_CODES,
    BuildError,
    DeploymentError,
    FailureCategory,
    HostError,
    InputError,
    InternalError,
    Interrupted,
    ProvisioningError,
    ScenarioError,
)
from sandbed.dev.results import SCHEMA_VERSION, emit, envelope, error_envelope


@pytest.mark.parametrize(
    ("cls", "category", "code"),
    [
        (InputError, "input", 2),
        (HostError, "host", 3),
        (ProvisioningError, "provisioning", 3),
        (BuildError, "build", 4),
        (DeploymentError, "deployment", 4),
        (ScenarioError, "scenario", 5),
        (InternalError, "internal", 6),
    ],
)
def test_exit_codes_are_the_published_contract(cls, category, code) -> None:
    err = cls("boom", stage="s")
    assert err.category.value == category
    assert err.exit_code == code


def test_interrupted_is_130_and_not_a_failure() -> None:
    err = Interrupted("stop")
    assert (err.exit_code, err.status) == (130, "interrupted")


def test_every_category_has_an_exit_code() -> None:
    assert set(EXIT_CODES) == set(FailureCategory)


def test_envelope_always_carries_the_identity_keys() -> None:
    payload = envelope("status", "succeeded")
    assert payload["schema_version"] == SCHEMA_VERSION
    assert {"lab_id", "label", "run_id"} <= payload.keys()
    assert payload["lab_id"] is None


def test_error_envelope_keeps_context_and_category() -> None:
    err = ScenarioError("no ssh", stage="guest-reachability", evidence=("/x/log",), lab_id="L")
    payload = error_envelope("test", err)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == "scenario"
    assert payload["failed_stage"] == "guest-reachability"
    assert payload["lab_id"] == "L"
    assert payload["evidence"] == ["/x/log"]


def test_json_mode_prints_exactly_one_object_on_stdout() -> None:
    @click.command()
    def cmd() -> None:
        emit(envelope("x", "succeeded"), json_mode=True)

    result = CliRunner().invoke(cmd)
    assert json.loads(result.output)["command"] == "x"
