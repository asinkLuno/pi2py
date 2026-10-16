"""Locate a Node.js runtime new enough for pi (>= 22.19.0)."""

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from pi2py.config import Pi2pyConfig

MIN_NODE = (22, 19, 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class NodeNotFoundError(Exception):
    """No usable Node.js runtime was found on this machine."""


def _parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.search(text.strip())
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# Cached per path so repeated spawns don't re-run `node --version`.
_checked: dict[str, tuple[int, int, int] | None] = {}


def _node_version(node: str) -> tuple[int, int, int] | None:
    if node in _checked:
        return _checked[node]
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, timeout=10, check=False
        ).stdout.decode("utf-8", errors="replace")
        version = _parse_version(out)
    except (OSError, subprocess.TimeoutExpired):
        version = None
    _checked[node] = version
    return version


def _candidates() -> Iterator[str]:
    # Explicit override: if set, it is the only candidate.
    if override := Pi2pyConfig().node:
        yield override
        return
    if found := shutil.which("node"):
        yield found
    home = Path.home()
    # nvm: pick the newest installed version
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        for d in sorted(nvm_root.iterdir(), reverse=True):
            candidate = d / "bin" / "node"
            if candidate.is_file():
                yield str(candidate)
    for path in (
        home / ".volta" / "bin" / "node",
        home / ".asdf" / "shims" / "node",
        Path("/usr/local/bin/node"),
        Path("/usr/bin/node"),
        Path("/opt/homebrew/bin/node"),
    ):
        if path.is_file():
            yield str(path)
    # fnm: newest installed version first
    for fnm_root in (
        home / ".local" / "share" / "fnm" / "node-versions",
        home / "Library" / "Application Support" / "fnm" / "node-versions",
    ):
        if fnm_root.is_dir():
            for d in sorted(fnm_root.iterdir(), reverse=True):
                candidate = d / "installation" / "node"
                if candidate.is_file():
                    yield str(candidate)


def find_node() -> str:
    """Return the path of a Node.js >= 22.19.0 runtime.

    Search order: $PI2PY_NODE, PATH, nvm (newest first), volta, fnm, asdf,
    common install locations. Raises NodeNotFoundError otherwise.
    """
    rejected: list[str] = []
    for candidate in _candidates():
        path = shutil.which(candidate) if os.sep not in candidate else candidate
        if not path or not os.path.isfile(path):
            rejected.append(f"{candidate} (missing)")
            continue
        if not os.access(path, os.X_OK):
            rejected.append(f"{path} (not executable)")
            continue
        version = _node_version(path)
        if version is None:
            rejected.append(f"{path} (could not determine version)")
            continue
        if version >= MIN_NODE:
            return path
        rejected.append(
            f"{path} ({version[0]}.{version[1]}.{version[2]} < {MIN_NODE[0]}.{MIN_NODE[1]}.{MIN_NODE[2]})"
        )
    detail = "\n  ".join(rejected) if rejected else "no candidates found"
    raise NodeNotFoundError(
        "pi2py needs Node.js >= 22.19.0 (pi's requirement).\n"
        f"Candidates considered:\n  {detail}\n"
        "Install one, e.g. `nvm install 22`, or point PI2PY_NODE at a node binary."
    )
