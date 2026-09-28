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

FrameForge runs from a published image, `ghcr.io/issaci22/frameforge`. You don't need to clone or build anything.

**1. Download the compose file** into a new folder:

```bash
mkdir frameforge && cd frameforge
curl -fsSLO https://raw.githubusercontent.com/issaci22/FrameForge/main/docker-compose.yml
```

Without its comments and optional settings, it looks like this:

```yaml
services:
  frameforge:
    image: ghcr.io/issaci22/frameforge:latest
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

**2. Tell it where your recordings are.** Create a `.env` file next to it, with your own values:

```bash
cat > .env <<'EOF'
# Host folder with your recordings; FrameForge sees it as /media
MEDIA_PATH=/mnt/storage/recordings
# User/group that owns your recordings (run `id` on Linux)
PUID=1000
PGID=1000
# Time zone for schedules and quiet hours
TZ=Europe/London
EOF
```

**3. Start it.** The first start downloads the image.

```bash
docker compose up -d
```

**4. Open `http://<your-server>:8686`** and follow the setup wizard. There's no default login: the wizard creates
your admin account, then walks through storage, transcoding, rules, nodes and schedule. Nothing besides the account
is saved until you confirm on the last step. The [first-time setup guide](docs/setup-guide.md) explains each step.

**Updating:** `docker compose pull && docker compose up -d`. To stay on one release instead of `latest`, pin its
tag (for example `ghcr.io/issaci22/frameforge:0.1.0`); see [docs/installation.md](docs/installation.md#image-tags).

On Windows, run these commands in Git Bash or WSL. In PowerShell, use `curl.exe` instead of `curl`, and create
`.env` in a text editor.

After setup, day-to-day work happens in three places:

- **Libraries:** the folders FrameForge watches. Paths are *inside the container*, for example `/media/vods`.
- **Rules:** an aging policy, or rules built visually. The live preview shows which files would be affected before anything runs.
- **Queue:** every job with its encoder, native quality value (e.g. "CRF 24"), progress, validation results and what happened to the original.

Volumes, permissions, backups and PostgreSQL are covered in [docs/installation.md](docs/installation.md).

## GPU acceleration

GPU encoding is one overlay file away. Download the overlay for your hardware into `docker/compose/`, next to your
`docker-compose.yml` (the same path as in the repository):

```bash
curl -fsSL --create-dirs -o docker/compose/gpu-nvidia.yml \
  https://raw.githubusercontent.com/issaci22/FrameForge/main/docker/compose/gpu-nvidia.yml
```

Use `gpu-intel-amd.yml` or `gpu-windows-nvidia.yml` in place of `gpu-nvidia.yml` for other hardware. Add the overlay
to your compose command, then check what actually works on your hardware. See [docs/gpu.md](docs/gpu.md) for host
requirements and multiple GPUs:

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

Development builds the image from your checkout instead of pulling it. `docker-compose.dev.yml` builds
`frameforge:dev` with the source mounted, and `docker/compose/build-local.yml` builds the production image locally.
See [docs/architecture.md](docs/architecture.md#development). Tests:

```bash
docker compose -f docker-compose.dev.yml run --rm dev pytest -q
```
