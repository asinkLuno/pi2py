"""First-run installation of the Node sidecar into the user cache.

The wheel ships src/pi2py/node/ (sidecar.mjs + package.json +
package-lock.json). At first use we copy those into a versioned cache
directory and run `npm ci` there, so the pi dependency tree is installed
exactly as locked, once, without touching site-packages.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from importlib.resources import files
from pathlib import Path

import platformdirs

from pi2py._node import find_node
from pi2py.config import Pi2pyConfig

SIDECAR_FILES = ("sidecar.mjs", "package.json", "package-lock.json")


class SidecarInstallError(Exception):
    """The Node sidecar dependency install failed."""


def _resource_bytes(name: str) -> bytes:
    return (files("pi2py") / "node" / name).read_bytes()


def _lock_hash() -> str:
    return hashlib.sha256(_resource_bytes("package-lock.json")).hexdigest()[:12]


def sidecar_cache_dir() -> Path:
    """Where the installed sidecar (with node_modules) lives.

    $PI2PY_SIDECAR_DIR overrides the default cache location (useful for
    tests and for sharing one install across projects).
    """
    if (override := Pi2pyConfig().sidecar_dir):
        return override
    return platformdirs.user_cache_path("pi2py") / f"sidecar-{_lock_hash()}"


def _npm_path() -> str:
    node = find_node()
    # npm ships with node; prefer the one sitting next to the binary.
    for sibling in (Path(node).with_name("npm"), Path(node).with_name("npm.cmd")):
        if sibling.is_file():
            return str(sibling)
    if (found := shutil.which("npm")):
        return found
    raise SidecarInstallError(f"npm not found next to {node} or on PATH")


def _run_npm_ci(directory: Path) -> None:
    npm = _npm_path()
    try:
        result = subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SidecarInstallError(f"`npm ci` failed to run in {directory}: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stdout + result.stderr)[-2000:]
        raise SidecarInstallError(
            f"`npm ci` failed in {directory}:\n{tail}\n"
            "This is a one-time install; simply retry. If it looks like a network issue, "
            "configure an npm registry mirror (npm_config_registry env var or ~/.npmrc)."
        )


def ensure_sidecar() -> Path:
    """Return a cache dir containing sidecar.mjs + node_modules, installing on first use."""
    target = sidecar_cache_dir()
    if (target / ".complete").is_file():
        return target

    staging = target.parent / f".staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True, exist_ok=True)
        for name in SIDECAR_FILES:
            (staging / name).write_bytes(_resource_bytes(name))
        print(f"pi2py: installing Node sidecar dependencies into {target} (one-time)", file=sys.stderr)
        _run_npm_ci(staging)
        (staging / ".complete").write_text("ok\n", encoding="utf-8")
        try:
            staging.rename(target)
        except OSError:
            if (target / ".complete").is_file():
                # Another process won the install race; use theirs.
                return target
            if target.exists():
                # Incomplete leftover from an older interrupted install.
                shutil.rmtree(target, ignore_errors=True)
                staging.rename(target)
            else:
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target
