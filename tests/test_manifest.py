import tempfile
from pathlib import Path

from sandbed.manifest import render


def test_render_shared_mount() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "user-data.yaml.tpl"
        p.write_text(
            "hostname: {{ hostname }}\n{% if shared_enabled %}- mkdir /shared\n{% endif %}\n"
        )
        out = render(str(p), {"hostname": "cloud-1", "shared_enabled": True})
        assert "- mkdir /shared" in out
        out2 = render(str(p), {"hostname": "cloud-1", "shared_enabled": False})
        assert "- mkdir /shared" not in out2