"""The resources `sandbed dev` reads at runtime must travel in the wheel (the Terraform roots
deliberately do not: dev labs need a source checkout)."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "sandbed" / "dev" / "data"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is needed to build the wheel")
def test_every_data_file_is_in_the_wheel_and_scripts_stay_executable(tmp_path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
        check=True, capture_output=True, text=True, timeout=300,
    )
    [wheel] = tmp_path.glob("sandbed-*.whl")
    with zipfile.ZipFile(wheel) as zf:
        entries = {i.filename: i for i in zf.infolist()}
    on_disk = sorted(p for p in DATA.iterdir() if p.is_file())
    assert on_disk
    for path in on_disk:
        member = f"sandbed/dev/data/{path.name}"
        assert member in entries, f"{member} is missing from the wheel"
        if path.suffix == ".sh":
            assert (entries[member].external_attr >> 16) & 0o111, f"{member} lost its exec bit"