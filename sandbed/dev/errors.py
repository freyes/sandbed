"""Failure taxonomy for the development-lab workflow.

Categories name the failing *layer*, not a proven root cause. Each maps to a stable exit code,
which is part of the public CLI contract.
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    INPUT = "input"
    HOST = "host"
    PROVISIONING = "provisioning"
    BUILD = "build"
    DEPLOYMENT = "deployment"
    SCENARIO = "scenario"
    INTERNAL = "internal"


EXIT_CODES: dict[FailureCategory, int] = {
    FailureCategory.INPUT: 2,
    FailureCategory.HOST: 3,
    FailureCategory.PROVISIONING: 3,
    FailureCategory.BUILD: 4,
    FailureCategory.DEPLOYMENT: 4,
    FailureCategory.SCENARIO: 5,
    FailureCategory.INTERNAL: 6,
}
EXIT_INTERRUPTED = 130


class DevError(Exception):
    """Base class. `context` carries ids (lab_id, run_id, ...) into the JSON envelope."""

    category: FailureCategory = FailureCategory.INTERNAL
    status = "failed"

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        evidence: tuple[str, ...] = (),
        **context: object,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.evidence = evidence
        self.context = context

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]


class InputError(DevError):
    category = FailureCategory.INPUT


class HostError(DevError):
    category = FailureCategory.HOST


class ProvisioningError(DevError):
    category = FailureCategory.PROVISIONING


class BuildError(DevError):
    category = FailureCategory.BUILD


class DeploymentError(DevError):
    category = FailureCategory.DEPLOYMENT


class ScenarioError(DevError):
    category = FailureCategory.SCENARIO


class InternalError(DevError):
    category = FailureCategory.INTERNAL


class Interrupted(DevError):
    """Operator interrupt. Says nothing about whether remote work stopped."""

    status = "interrupted"

    @property
    def exit_code(self) -> int:
        return EXIT_INTERRUPTED
