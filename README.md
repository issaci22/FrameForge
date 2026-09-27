# FrameForge

**Self-hosted video compression for creators.** Keep your recent recordings untouched. After *N* days,
FrameForge compresses older footage into a smaller format on whatever hardware you have (CPU, NVIDIA,
Intel or AMD), and it never replaces a file until the new one has been checked.

It's built for YouTubers, streamers and editors with terabytes of OBS recordings, stream VODs and
raw footage on a NAS or home server.

- **Aging policies:** for example, "0–30 days: keep, 30–365 days: H.265, 1 year+: AV1". Set it up once and FrameForge applies it on every scan.
- **Safe by design:** outputs are written to a hidden temp folder, validated (probe, streams, duration,
  decode checks), moved into place, and re-checked. Only then is the original backed up or removed.
  Every step is journaled, so a crash or power cut at any point can be rolled forward or back.
- **Real hardware detection:** a GPU encoder is only used after it passes a real test encode on that machine.
- **Distributed:** add more machines as transcoding nodes. They connect *out* to the server, so no ports need to be opened on them.
- **Human explanations:** failures come with a plain-language diagnosis and likely causes. The raw FFmpeg log is one click away.

FrameForge is an independent project. It is not affiliated with Tdarr or based on its code.

## Quick start

The normal install is **one container on one machine**: the web UI, the server and a built-in transcoding node.
It encodes on the CPU out of the box; a GPU is an optional add-on ([below](#gpu-acceleration)). You need Docker
with Compose. On Linux, Docker Engine is recommended. Docker Desktop works for CPU and NVIDIA.

This is the included `docker-compose.yml`, without its comments and optional settings:

```yaml
services:
  frameforge:
    build:
      context: .
      dockerfile: docker/Dockerfile
    image: frameforge:latest
    container_name: frameforge
    restart: unless-stopped
    ports:
      - "8686:8686"
    environment:
      - PUID=${PUID:-1000}
      - PGID=${PGID:-1000}
      - TZ=${TZ:-Etc/UTC}
    volumes:
      - ./config:/config                  # database, logs, node state
      - ${MEDIA_PATH:-./media}:/media     # your recordings (read-write)
    stop_grace_period: 60s                # lets running jobs finish moving files
```

**1. Get FrameForge.** There's no prebuilt image yet, so Compose builds it from the repository.

```bash
git clone https://github.com/issaci22/FrameForge.git
cd FrameForge
```

**2. Tell it where your recordings are.** Copy `.env.example` to `.env` and set:

```bash
MEDIA_PATH=/mnt/storage/recordings   # host folder; FrameForge sees it as /media
PUID=1000                            # user/group that owns your recordings (`id` on Linux)
PGID=1000
TZ=Europe/London                     # used for schedules and quiet hours
```

**3. Start it.** The first build takes a few minutes.

```bash
docker compose up -d --build
```

**4. Open `http://<your-server>:8686`** and follow the setup wizard. There's no default login: the wizard creates
your admin account, then walks through storage, transcoding, rules, nodes and schedule. Nothing besides the account
is saved until you confirm on the last step. The [first-time setup guide](docs/setup-guide.md) explains each step.

To update later, run `git pull` and the same `docker compose up -d --build`.

After setup, day-to-day work happens in three places:

- **Libraries:** the folders FrameForge watches. Paths are *inside the container*, for example `/media/vods`.
- **Rules:** an aging policy, or rules built visually. The live preview shows which files would be affected before anything runs.
- **Queue:** every job with its encoder, native quality value (e.g. "CRF 24"), progress, validation results and what happened to the original.

Volumes, permissions, backups and PostgreSQL are covered in [docs/installation.md](docs/installation.md).

## GPU acceleration

GPU encoding is one overlay file away. Add it to your compose command, then check what actually works on your
hardware. See [docs/gpu.md](docs/gpu.md) for host requirements and multiple GPUs:

```bash
docker compose -f docker-compose.yml -f docker/compose/gpu-intel-amd.yml up -d   # Intel QSV / VA-API, AMD VA-API
docker compose -f docker-compose.yml -f docker/compose/gpu-nvidia.yml up -d      # NVIDIA NVENC
docker compose -f docker-compose.yml -f docker/compose/gpu-windows-nvidia.yml up -d   # NVIDIA on Windows (Docker Desktop)
docker compose exec frameforge python -m frameforge_node --detect                 # what actually works
```

## Documentation

| | |
|---|---|
| [First-time setup](docs/setup-guide.md) | Step by step from the setup wizard to automated, tested compression |
| [Installation](docs/installation.md) | Docker setup, volumes, PUID/PGID, updating, backups |
| [GPU acceleration](docs/gpu.md) | NVIDIA (Linux and Windows/WSL2), Intel (Quick Sync / Arc), AMD, and multiple GPUs |
| [Nodes](docs/nodes.md) | Adding transcoding machines, path mappings, limits |
| [Libraries](docs/libraries.md) | Folders, scanning, where converted files go, and keeping or deleting originals (including timed retention) |
| [Profiles](docs/profiles.md) | Goal-based built-in profiles, format and audio choices, the quality slider, compression previews |
| [Rules](docs/rules.md) | Aging policies, the rule builder, re-processing guards |
| [Scheduling](docs/scheduling.md) | Priorities, quiet hours, node time windows, utilization limits |
| [Troubleshooting](docs/troubleshooting.md) | Every diagnosis FrameForge can show, and what to do |
| [Architecture](docs/architecture.md) | How it works inside: for contributors |

## Status

Version 0.1: the core pipeline works end to end.
- Scanning, rules, scheduling, CPU and NVENC transcoding, validation, safe replacement, remote nodes and crash recovery all work.
- NVENC has been verified on real hardware. QSV and VA-API are built and detected, but haven't been exercised on real Intel/AMD hardware yet.

Not built yet: file transfer to nodes without shared storage, the HandBrake engine (listed but reported unavailable), OIDC/API tokens.

## Development

See [docs/architecture.md](docs/architecture.md#development). Tests:

```bash
docker compose -f docker-compose.dev.yml run --rm dev pytest -q
```
