"""pi2py — call pi's agent runtime (@earendil-works/pi-agent-core) from Python.

M1 ships the transport layer: node discovery, first-run sidecar install, and
a sync JSONL protocol client (:class:`PiProcess`). On top of it, the
agno-style :class:`Agent` facade runs prompts over any models.json provider.
"""

from pi2py._install import SidecarInstallError, ensure_sidecar, sidecar_cache_dir
from pi2py._node import MIN_NODE, NodeNotFoundError, find_node
from pi2py.agent import Agent, RunOutput
from pi2py.config import Pi2pyConfig
from pi2py.transport import (
    PiProcess,
    PiProcessError,
    SidecarCommandError,
    SidecarTimeout,
)

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "MIN_NODE",
    "NodeNotFoundError",
    "Pi2pyConfig",
    "PiProcess",
    "PiProcessError",
    "RunOutput",
    "SidecarCommandError",
    "SidecarInstallError",
    "SidecarTimeout",
    "ensure_sidecar",
    "find_node",
    "sidecar_cache_dir",
]
