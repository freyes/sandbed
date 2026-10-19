from unittest.mock import patch

import pytest

from sandbed.config import load_config
from sandbed.preflight import PreflightError, check


class _Fake:
    def __init__(self, code: int) -> None:
        self.returncode = code


@patch("sandbed.preflight.os.path.exists", return_value=True)
@patch("sandbed.preflight.run", side_effect=lambda *a, **k: _Fake(0))
def test_check_ok(fake_run, fake_exists) -> None:
    check(load_config("manual", "up"))


@patch("sandbed.preflight.os.path.exists", return_value=False)
def test_check_no_kvm(fake_exists) -> None:
    with pytest.raises(PreflightError):
        check(load_config("manual", "up"))