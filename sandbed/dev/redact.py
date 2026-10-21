"""Best-effort scrubbing of text before it is exported. Raw files stay private; only redacted
copies of allowlisted outputs leave the lab. This is a safety net, not the primary control."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?im)^(\s*export\s+)?(OS_PASSWORD|OS_APPLICATION_CREDENTIAL_SECRET)=.*$"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|credential)(\"?\s*[:=]\s*\"?)"
        r"[^\s\"',}]+"
    ),
    re.compile(r"(?i)\b(bearer|x-auth-token:?)\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\bgAAAA[A-Za-z0-9_=-]{20,}"),  # Keystone fernet tokens
]


def redact(text: str, extra: tuple[str, ...] = ()) -> str:
    """Replace known secret shapes, plus any literal in `extra` (e.g. a seeded test credential)."""
    for literal in extra:
        if literal:
            text = text.replace(literal, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text