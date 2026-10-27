import os
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_ssh_key_generation(monkeypatch):
    """CLI tests must not create a real key in the working directory.

    Only the name `sandbed.cli.sshkey` is replaced; tests of the sshkey module itself import the
    real module directly.
    """
    monkeypatch.setattr("sandbed.cli.sshkey", SimpleNamespace(prepare=lambda ctx: None))


def install_systemd_guard(monkeypatch) -> None:
    """Make an unpatched `systemd._popen` / `systemd._run` fail loudly.

    A no-op when SANDBED_DEV_INTEGRATION=1 (the real-systemd acceptance test, never set in CI).
    """
    if os.environ.get("SANDBED_DEV_INTEGRATION") == "1":
        return

    def forbidden(name):
        def guard(*args, **kwargs):
            raise AssertionError(
                f"test reached the real systemd.{name}{args!r}: patch the seam "
                f"(monkeypatch.setattr('sandbed.dev.systemd.{name}', fake)) so no real unit runs"
            )

        return guard

    monkeypatch.setattr("sandbed.dev.systemd._popen", forbidden("_popen"))
    monkeypatch.setattr("sandbed.dev.systemd._run", forbidden("_run"))


@pytest.fixture(autouse=True)
def _no_real_systemd(monkeypatch):
    install_systemd_guard(monkeypatch)
