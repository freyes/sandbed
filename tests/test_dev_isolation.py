"""Architecture guard: the development workflow must not reach into legacy phase machinery."""

from __future__ import annotations

import ast
from pathlib import Path

DEV = Path(__file__).resolve().parents[1] / "sandbed" / "dev"

# `from ..<module> import ...` allowed from sandbed.dev, with the names it may take.
ALLOWED_PARENT_IMPORTS = {
    "shell": {"run"},
    "sshkey": {"ensure_key"},
}


def _parent_imports(path: Path) -> list[tuple[str, set[str]]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 2 and node.module:
            found.append((node.module, {a.name for a in node.names}))
        elif isinstance(node, ast.ImportFrom) and node.level == 2 and not node.module:
            found.append(("", {a.name for a in node.names}))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.startswith("sandbed.") and not node.module.startswith("sandbed.dev"):
                found.append((node.module.split(".", 1)[1], {a.name for a in node.names}))
    return found


def test_dev_only_imports_the_allowed_legacy_helpers() -> None:
    for path in DEV.glob("*.py"):
        for module, names in _parent_imports(path):
            assert module in ALLOWED_PARENT_IMPORTS, f"{path.name} imports legacy {module}"
            assert names <= ALLOWED_PARENT_IMPORTS[module], f"{path.name}: {module} {names}"
