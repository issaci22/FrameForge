"""Code shared by the FrameForge server and node agent.

This package must never import from ``frameforge_server`` or ``frameforge_node``.
"""

__version__ = "0.1.0"

# Bumped whenever the node <-> server websocket protocol changes incompatibly.
PROTOCOL_VERSION = 1
