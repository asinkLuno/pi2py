"""Config layer: PI2PY_* env vars flow through Pi2pyConfig."""

from pathlib import Path

import pytest

from pi2py import Pi2pyConfig, sidecar_cache_dir


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PI2PY_NODE", "node22")
    monkeypatch.setenv("PI2PY_SIDECAR_DIR", "/tmp/pi2py-sidecar")
    config = Pi2pyConfig()
    assert config.node == "node22"
    assert config.sidecar_dir == Path("/tmp/pi2py-sidecar")
    assert sidecar_cache_dir() == Path("/tmp/pi2py-sidecar")


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PI2PY_NODE", raising=False)
    monkeypatch.delenv("PI2PY_SIDECAR_DIR", raising=False)
    assert Pi2pyConfig().node is None
    assert Pi2pyConfig().sidecar_dir is None
