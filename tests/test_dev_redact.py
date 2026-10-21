from __future__ import annotations

from sandbed.dev.redact import REDACTED, redact


def test_redaction_scrubs_known_shapes_and_literals() -> None:
    text = (
        "export OS_PASSWORD=hunter2\n"
        'config: {"password": "s3cret", "token": "abc"}\n'
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        "seeded=CANARY-123\nplain line\n"
    )
    out = redact(text, extra=("CANARY-123",))
    for leaked in ("hunter2", "s3cret", "AAAA", "CANARY-123", '"abc"'):
        assert leaked not in out
    assert "plain line" in out and REDACTED in out