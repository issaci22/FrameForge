"""Generate copy-paste deployment snippets for new nodes."""

from __future__ import annotations

from typing import Literal

Hardware = Literal["cpu", "nvidia", "intel", "amd"]

_GPU_BLOCKS: dict[str, str] = {
    "cpu": "",
    "nvidia": """    # Requires the NVIDIA driver + NVIDIA Container Toolkit on the host (see docs/gpu.md)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu, video, compute, utility]
""",
    "intel": """    # Intel Quick Sync / VA-API: pass the render device through (see docs/gpu.md)
    devices:
      - /dev/dri:/dev/dri
""",
    "amd": """    # AMD VA-API: pass the render device through (see docs/gpu.md)
    devices:
      - /dev/dri:/dev/dri
""",
}

_RUN_FLAGS: dict[str, str] = {
    "cpu": "",
    "nvidia": " \\\n  --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all",
    "intel": " \\\n  --device /dev/dri:/dev/dri",
    "amd": " \\\n  --device /dev/dri:/dev/dri",
}


def compose_snippet(server_url: str, token: str, hardware: Hardware, media_paths: list[str]) -> str:
    env_extra = "      - NVIDIA_DRIVER_CAPABILITIES=all\n" if hardware == "nvidia" else ""
    volumes = "".join(f"      - {p}:{p}  # same path as on the server (or add a path mapping)\n" for p in media_paths) or "      - /path/to/media:/media\n"
    return f"""services:
  frameforge-node:
    image: frameforge:latest  # build it from the FrameForge repo on this machine (docs/nodes.md)
    container_name: frameforge-node
    restart: unless-stopped
    environment:
      - FF_ROLE=node
      - FF_SERVER_URL={server_url}
      - FF_NODE_TOKEN={token}
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
{env_extra}    volumes:
      - ./frameforge-node:/config
{volumes}{_GPU_BLOCKS[hardware]}"""


def docker_run_snippet(server_url: str, token: str, hardware: Hardware, media_paths: list[str]) -> str:
    vols = "".join(f" \\\n  -v {p}:{p}" for p in media_paths) or " \\\n  -v /path/to/media:/media"
    return (
        "docker run -d --name frameforge-node --restart unless-stopped \\\n"
        f"  -e FF_ROLE=node -e FF_SERVER_URL={server_url} \\\n"
        f"  -e FF_NODE_TOKEN={token} -e PUID=1000 -e PGID=1000 \\\n"
        f"  -v $PWD/frameforge-node:/config{vols}{_RUN_FLAGS[hardware]} \\\n"
        "  frameforge:latest"
    )
