"""Static checks for scripts/prep-host.sh: the SANDBED_PREP_MICROCEPH opt-out guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "prep-host.sh"


def test_prep_host_sh_is_syntactically_valid() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_prep_host_sh_text() -> None:
    text = SCRIPT.read_text()

    assert "SANDBED_PREP_MICROCEPH" in text
    assert 'PREP_MICROCEPH="${SANDBED_PREP_MICROCEPH:-1}"' in text
    assert 'microceph disk add "loop,${OSD_SIZE},3"' in text
    assert 'snap install "${snap}"' in text
    assert 'echo "microceph: skipped (SANDBED_PREP_MICROCEPH=0)"' in text