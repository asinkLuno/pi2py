"""pi2py configuration, sourced from PI2PY_* environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Pi2pyConfig(BaseSettings):
    """Runtime configuration; every field maps to $PI2PY_<FIELD>.

    Constructed at read time rather than as a module singleton so env
    changes after import (e.g. test fixtures) are honored.
    """

    model_config = SettingsConfigDict(env_prefix="PI2PY_")

    node: str | None = None
    sidecar_dir: Path | None = None
