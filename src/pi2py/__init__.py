"""pi2py — call pi's agent runtime (@earendil-works/pi-agent-core) from Python.

M1 ships the transport layer: node discovery, first-run sidecar install, and
a sync JSONL protocol client (:class:`PiProcess`). The agno-style Agent
facade is built on top of this.
"""

from pi2py._install import SidecarInstallError, ensure_sidecar, sidecar_cache_dir
from pi2py._node import MIN_NODE, NodeNotFoundError, find_node
from pi2py.transport import (
    PiProcess,
    PiProcessError,
    SidecarCommandError,
    SidecarTimeout,
)

__version__ = "0.1.0"

__all__ = [
    "MIN_NODE",
    "NodeNotFoundError",
    "PiProcess",
    "PiProcessError",
    "SidecarCommandError",
    "SidecarInstallError",
    "SidecarTimeout",
    "ensure_sidecar",
    "find_node",
    "sidecar_cache_dir",
]
