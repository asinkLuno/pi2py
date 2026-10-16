"""Shared fixtures: hermetic sidecar install dir for the whole session."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def sidecar_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    os.environ["PI2PY_SIDECAR_DIR"] = str(tmp_path_factory.mktemp("sidecar"))
