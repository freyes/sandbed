"""Architecture guard: the development workflow must not reach into legacy phase machinery."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DEV = Path(__file__).resolve().parents[1] / "sandbed" / "dev"

# Legacy sandbed modules the dev workflow may import from, with the names it may take.
ALLOWED_PARENT_IMPORTS: dict[str, set[str]] = {
    "shell": {"run"},
    "sshkey": {"ensure_key"},
}


def _legacy_imports(source: str) -> list[tuple[str, set[str] | None]]:
    """Legacy sandbed imports (anything not under ``sandbed.dev``) found in ``source``.

    Each entry is ``(module, names)``: ``names`` is the set of imported names for
    ``from``-imports, or ``None`` for ``import sandbed.<module>`` form imports.
    """
    found: list[tuple[str, set[str] | None]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "sandbed" or (
                    name.startswith("sandbed.") and not name.startswith("sandbed.dev")
                ):
                    parts = name.split(".", 1)
                    found.append((parts[1] if len(parts) > 1 else "", None))
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                found.append((node.module or "", {a.name for a in node.names}))
            elif node.level == 0 and node.module:
                if node.module == "sandbed":
                    found.append(("", {a.name for a in node.names}))
                elif node.module.startswith("sandbed.") and not node.module.startswith(
                    "sandbed.dev"
                ):
                    found.append(
                        (node.module.split(".", 1)[1], {a.name for a in node.names})
                    )
    return found


def _check_legacy_imports(source: str) -> list[str]:
    """Violation messages for any disallowed legacy import in ``source``."""
    violations: list[str] = []
    for module, names in _legacy_imports(source):
        if names is None:
            violations.append(f"forbidden import form: sandbed.{module or 'sandbed'}")
        elif module not in ALLOWED_PARENT_IMPORTS:
            violations.append(f"imports legacy {module or 'sandbed'}")
        elif not names <= ALLOWED_PARENT_IMPORTS[module]:
            violations.append(f"forbidden names from {module}: {names}")
    return violations


# --- parametrized self-tests: allowed import forms -------------------------

_ALLOWED_SNIPPETS = [
    "from sandbed.dev.errors import DevError",
    "from sandbed.dev import errors",
    "import sandbed.dev.errors",
    "import sandbed.dev",
    "from .errors import DevError",
    "from . import errors",
    "from ..shell import run",
    "from ..sshkey import ensure_key",
    "from sandbed.shell import run",
    "from sandbed.sshkey import ensure_key",
    "import os",
    "from pathlib import Path",
    "import subprocess",
    "import click",
]


@pytest.mark.parametrize("snippet", _ALLOWED_SNIPPETS)
def test_isolation_allows(snippet: str) -> None:
    assert _check_legacy_imports(snippet) == []


# --- parametrized self-tests: forbidden import forms ------------------------

_FORBIDDEN_SNIPPETS = [
    "import sandbed",
    "import sandbed.shell",
    "from sandbed import shell",
    "from sandbed import shell, sshkey",
    "from sandbed.shell import run, is_transient_connection_error",
    "from sandbed.console import console",
    "from .. import shell",
    "from .. import os",
    "from ..console import console",
    "from ..shell import run, is_transient_connection_error",
]


@pytest.mark.parametrize("snippet", _FORBIDDEN_SNIPPETS)
def test_isolation_rejects(snippet: str) -> None:
    assert _check_legacy_imports(snippet) != []


# --- real scan of sandbed/dev/*.py -----------------------------------------

def test_dev_only_imports_the_allowed_legacy_helpers() -> None:
    for path in DEV.glob("*.py"):
        violations = _check_legacy_imports(path.read_text())
        assert not violations, f"{path.name}: {violations}"