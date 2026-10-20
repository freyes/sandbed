from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_ssh_key_generation(monkeypatch):
    """CLI tests must not create a real key in the working directory.

    Only the name `sandbed.cli.sshkey` is replaced; tests of the sshkey module itself import the
    real module directly.
    """
    monkeypatch.setattr("sandbed.cli.sshkey", SimpleNamespace(prepare=lambda ctx: None))
