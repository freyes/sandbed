from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbed import sshkey
from sandbed.config import load_config


def _fake_keygen(pub: str = "ssh-ed25519 AAAA sandbed"):
    """Stand-in for ssh-keygen: writes the key files like the real tool would."""

    def fake(cmd, **_kwargs):
        if "-f" in cmd and "-y" not in cmd:
            private = Path(cmd[cmd.index("-f") + 1])
            private.write_text("PRIVATE")
            Path(f"{private}.pub").write_text(pub + "\n")
        out = MagicMock()
        out.stdout = pub + "\n"
        return out

    return fake


@patch("sandbed.sshkey.run")
def test_ensure_key_creates_a_passwordless_ed25519_key(fake_run, tmp_path) -> None:
    fake_run.side_effect = _fake_keygen()
    path = tmp_path / "key"
    assert sshkey.ensure_key(str(path)) == "ssh-ed25519 AAAA sandbed"
    fake_run.assert_called_once_with(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "sandbed", "-f", str(path)]
    )


@patch("sandbed.sshkey.run")
def test_ensure_key_never_overwrites_an_existing_key(fake_run, tmp_path) -> None:
    (tmp_path / "key").write_text("EXISTING")
    (tmp_path / "key.pub").write_text("ssh-ed25519 OLD me\n")
    assert sshkey.ensure_key(str(tmp_path / "key")) == "ssh-ed25519 OLD me"
    fake_run.assert_not_called()
    assert (tmp_path / "key").read_text() == "EXISTING"


@patch("sandbed.sshkey.run")
def test_ensure_key_derives_a_missing_public_half(fake_run, tmp_path) -> None:
    (tmp_path / "key").write_text("EXISTING")
    fake_run.side_effect = _fake_keygen("ssh-ed25519 DERIVED sandbed")
    assert sshkey.ensure_key(str(tmp_path / "key")) == "ssh-ed25519 DERIVED sandbed"
    fake_run.assert_called_once_with(["ssh-keygen", "-y", "-f", str(tmp_path / "key")])


@patch("sandbed.sshkey.run")
def test_prepare_fills_ssh_key_and_identity(fake_run, tmp_path, monkeypatch) -> None:
    fake_run.side_effect = _fake_keygen()
    monkeypatch.chdir(tmp_path)
    ctx = load_config("maas", "up", ssh_key_file=".sandbed_ed25519")
    sshkey.prepare(ctx)
    assert ctx.ssh_key == "ssh-ed25519 AAAA sandbed"
    assert ctx.ssh_identity == str(tmp_path / ".sandbed_ed25519")


@patch("sandbed.sshkey.run")
def test_prepare_reuses_the_key_on_a_second_call(fake_run, tmp_path, monkeypatch) -> None:
    fake_run.side_effect = _fake_keygen()
    monkeypatch.chdir(tmp_path)
    sshkey.prepare(load_config("manual", "up", ssh_key_file="k"))
    sshkey.prepare(load_config("manual", "up", ssh_key_file="k"))
    assert fake_run.call_count == 1


@patch("sandbed.sshkey.run")
def test_prepare_leaves_an_explicit_ssh_key_alone(fake_run, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ctx = load_config("maas", "up", ssh_key="ssh-ed25519 MINE", ssh_key_file="k")
    sshkey.prepare(ctx)
    assert ctx.ssh_key == "ssh-ed25519 MINE"
    assert ctx.ssh_identity == ""
    fake_run.assert_not_called()
    assert not (tmp_path / "k").exists()
