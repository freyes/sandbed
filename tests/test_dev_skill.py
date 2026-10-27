from __future__ import annotations

import re
from pathlib import Path

import yaml

from sandbed.cli import cli
from sandbed.dev.capabilities import INTERFACE_VERSION, command_paths
from sandbed.dev.cli import dev
from sandbed.dev.errors import EXIT_CODES

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "snap-openstack-dev"
FILES = [SKILL_DIR / "SKILL.md", *sorted((SKILL_DIR / "references").glob("*.md"))]


def test_skill_front_matter_is_valid() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text()
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == "snap-openstack-dev"
    assert meta["description"].startswith("Use when")


def test_every_command_the_skill_mentions_exists() -> None:
    valid = set(command_paths(dev))
    assert "dev" in cli.commands
    mentioned = set()
    for path in FILES:
        mentioned |= set(re.findall(r"sandbed dev ((?:host |job )?[a-z]+)", path.read_text()))
    assert mentioned and mentioned <= valid, mentioned - valid


def test_every_published_command_is_documented_in_the_skill() -> None:
    text = "\n".join(p.read_text() for p in FILES)
    for command in set(command_paths(dev)) - {"host prepare"}:
        assert f"sandbed dev {command}" in text or f"`{command}`" in text, command


def test_exit_code_table_matches_the_contract() -> None:
    table = (SKILL_DIR / "SKILL.md").read_text()
    for code in {*EXIT_CODES.values(), 130, 0}:
        assert re.search(rf"^\| {code} \|", table, re.M), code


def test_skill_requires_the_current_interface_version() -> None:
    assert f"`interface_version` is `{INTERFACE_VERSION}`" in (SKILL_DIR / "SKILL.md").read_text()


def test_skill_job_fields_exist() -> None:
    text = "\n".join(p.read_text() for p in FILES)
    source = (Path(__file__).resolve().parents[1] / "sandbed" / "dev" / "jobcmds.py").read_text()
    for documented, key in [
        ("job.state", "state"), ("current_stage", "current_stage"),
        ("timed_out", "timed_out"), ("next_offset", "next_offset"),
        ("cancelled", "cancelled"),
    ]:
        assert documented in text, documented
        assert f'"{key}"' in source, key


def test_skill_checks_fail_when_a_command_is_undocumented(monkeypatch, tmp_path) -> None:
    import pytest

    copies = []
    for path in FILES:
        copy = tmp_path / path.name
        copy.write_text(path.read_text().replace("sandbed dev job wait", "REMOVED"))
        copies.append(copy)
    monkeypatch.setitem(globals(), "FILES", copies)
    with pytest.raises(AssertionError):
        test_every_published_command_is_documented_in_the_skill()
