from __future__ import annotations

import io
import json
import tarfile

from sandbed.dev.collect import (
    FAILED,
    OK,
    TIMEOUT,
    UNAVAILABLE,
    Collector,
    collect,
    make_archive,
    sanitize_archive,
)

CANARY = "CANARY-SECRET-42"


def col(name: str, **kw) -> Collector:
    return Collector(name, ("true",), "guest", **kw)


def run(collectors, answers, tmp_path, extra=()):
    def execute(c: Collector):
        return answers[c.name]

    return collect(
        collectors, execute, raw_dir=tmp_path / "raw", out_dir=tmp_path / "out", extra=extra
    )


def test_every_collector_gets_an_entry_and_failures_do_not_stop_the_rest(tmp_path) -> None:
    answers = {
        "a": (OK, "fine", ""),
        "b": (TIMEOUT, "partial", "timed out after 5s"),
        "c": (UNAVAILABLE, "", "juju: command not found"),
        "d": (FAILED, "", "exit 1"),
    }
    index = run([col(n) for n in answers], answers, tmp_path)
    assert [e["status"] for e in index["entries"]] == [OK, TIMEOUT, UNAVAILABLE, FAILED]
    assert index["complete"] is False
    assert json.loads((tmp_path / "out" / "index.json").read_text()) == index


def test_an_executor_that_raises_is_recorded_not_propagated(tmp_path) -> None:
    def boom(_c):
        raise RuntimeError("kaboom")

    index = collect([col("a")], boom, raw_dir=tmp_path / "r", out_dir=tmp_path / "o")
    assert index["entries"][0]["status"] == FAILED
    assert "kaboom" in index["entries"][0]["error"]


def test_exports_are_redacted_but_raw_stays_private(tmp_path) -> None:
    index = run([col("a")], {"a": (OK, f"password: {CANARY}\nkeep", "")}, tmp_path, (CANARY,))
    exported = (tmp_path / "out" / "a.txt").read_text()
    assert CANARY not in exported and "keep" in exported
    assert CANARY in (tmp_path / "raw" / "a.txt").read_text()
    assert index["entries"][0]["sha256"]


def test_size_cap_truncates_and_says_so(tmp_path) -> None:
    index = run([col("a", max_bytes=10)], {"a": (OK, "x" * 100, "")}, tmp_path)
    assert index["entries"][0]["truncated"] is True
    assert (tmp_path / "out" / "a.txt").read_text().startswith("x" * 10 + "\n[truncated")


def build_tar(path, members: dict[str, bytes], extra=None) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)


def test_inspection_archive_members_are_filtered_and_omissions_recorded(tmp_path) -> None:
    link = tarfile.TarInfo("link.log")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
    build_tar(
        tmp_path / "in.tar.gz",
        {
            "report/juju-status.txt": f"token: {CANARY}\nok".encode(),
            "report/openrc": b"export OS_PASSWORD=x",
            "report/id_ed25519": b"-----BEGIN OPENSSH PRIVATE KEY-----",
            "report/terraform.tfstate": b"{}",
            "report/blob.bin": b"\x00\x01",
            "../escape.log": b"x",
            "report/huge.log": b"x" * 50,
        },
        extra=[link],
    )
    omitted = sanitize_archive(
        tmp_path / "in.tar.gz", tmp_path / "out", max_member_bytes=40, extra=(CANARY,)
    )
    exported = tmp_path / "out" / "report" / "juju-status.txt"
    assert [p.name for p in (tmp_path / "out").rglob("*") if p.is_file()] == ["juju-status.txt"]
    assert CANARY not in exported.read_text()
    assert exported.stat().st_mode & 0o777 == 0o600
    reasons = {o["member"]: o["reason"] for o in omitted}
    assert reasons["report/openrc"] == "sensitive name"
    assert reasons["report/id_ed25519"] == "sensitive name"
    assert reasons["report/terraform.tfstate"] == "sensitive name"
    assert reasons["report/blob.bin"] == "not an allowlisted text type"
    assert reasons["../escape.log"] == "unsafe path"
    assert reasons["report/huge.log"] == "too large"
    assert reasons["link.log"] == "not a regular file"


def test_members_with_the_same_basename_do_not_overwrite_each_other(tmp_path) -> None:
    build_tar(tmp_path / "in.tar.gz", {"a/status.log": b"from a", "b/status.log": b"from b"})
    with tarfile.open(tmp_path / "dup.tar.gz", "w:gz") as tar:
        for name, data in (("a/status.log", b"first"), ("a/status.log", b"again")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    sanitize_archive(tmp_path / "in.tar.gz", tmp_path / "out")
    assert (tmp_path / "out/a/status.log").read_text() == "from a"
    assert (tmp_path / "out/b/status.log").read_text() == "from b"
    omitted = sanitize_archive(tmp_path / "dup.tar.gz", tmp_path / "out2")
    assert (tmp_path / "out2/a/status.log").read_text() == "first"
    assert omitted == [{"member": "a/status.log", "reason": "duplicate member"}]


def test_a_file_and_a_directory_of_the_same_name_cannot_abort_the_export(tmp_path) -> None:
    build_tar(tmp_path / "a.tar.gz", {"report": b"x", "report/juju-status.txt": b"y"})
    build_tar(tmp_path / "b.tar.gz", {"report/juju-status.txt": b"y", "report": b"x"})
    first = sanitize_archive(tmp_path / "a.tar.gz", tmp_path / "out-a")
    second = sanitize_archive(tmp_path / "b.tar.gz", tmp_path / "out-b")
    # `report` has no allowlisted suffix, so only the nested file can be exported; a
    # suffix-bearing file/dir clash is exercised below.
    assert first == [{"member": "report", "reason": "not an allowlisted text type"}]
    assert second == [{"member": "report", "reason": "not an allowlisted text type"}]
    build_tar(tmp_path / "c.tar.gz", {"x.log": b"file", "x.log/y.log": b"nested"})
    build_tar(tmp_path / "d.tar.gz", {"x.log/y.log": b"nested", "x.log": b"file"})
    c = sanitize_archive(tmp_path / "c.tar.gz", tmp_path / "out-c")
    d = sanitize_archive(tmp_path / "d.tar.gz", tmp_path / "out-d")
    assert c == [{"member": "x.log/y.log", "reason": "path conflicts with another member"}]
    assert d == [{"member": "x.log", "reason": "path conflicts with another member"}]
    assert (tmp_path / "out-c/x.log").read_text() == "file"
    assert (tmp_path / "out-d/x.log/y.log").read_text() == "nested"


def test_collected_files_are_private(tmp_path) -> None:
    run([col("a")], {"a": (OK, "x", "")}, tmp_path)
    for path in (tmp_path / "raw/a.txt", tmp_path / "out/a.txt", tmp_path / "out/index.json"):
        assert path.stat().st_mode & 0o777 == 0o600, path


def test_archive_contains_only_the_export_directory(tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.txt").write_text("hello")
    (tmp_path / "private.txt").write_text(CANARY)
    archive = make_archive(out, tmp_path / "x" / "diagnostics.tar.gz")
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["a.txt"]