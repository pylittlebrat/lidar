"""重试工具测试。"""

from __future__ import annotations

import pytest

from techradar.utils.retry import retry


def test_retry_succeeds_after_failures() -> None:
    calls = {"n": 0}

    @retry(max_attempts=3, base_delay=0)
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_exhausts_raises() -> None:
    calls = {"n": 0}

    @retry(max_attempts=2, base_delay=0)
    def always_fail() -> None:
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        always_fail()
    assert calls["n"] == 2


def test_retry_ignores_other_exceptions() -> None:
    calls = {"n": 0}

    @retry(max_attempts=3, base_delay=0, exceptions=(TimeoutError,))
    def wrong_type() -> None:
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        wrong_type()
    assert calls["n"] == 1