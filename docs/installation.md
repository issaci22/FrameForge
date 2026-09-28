# Installation

FrameForge ships as one Docker image. `FF_ROLE` picks what a container does:

| `FF_ROLE` | Runs | Use it for |
|---|---|---|
| `all` (default) | Web UI + API + a built-in transcoding node | A single machine. Start here. |
| `server` | Web UI + API only | A NAS or small box that stores media but shouldn't transcode |
| `node` | A transcoding node that connects to a server | Extra machines or GPUs. See [nodes.md](nodes.md) |

## Requirements

- Docker with Compose v2. On Linux, Docker Engine is recommended. Docker Desktop (Windows/macOS) works for
  CPU and NVIDIA, but **cannot** pass Intel/AMD GPUs through.
- Nothing is built on the host. FrameForge runs from the published image `ghcr.io/issaci22/frameforge`
  (linux/amd64), so no Node.js, Python or source checkout is needed.
- Disk: transcodes are written next to their final location while they run, so keep free space of roughly
  one output file per running job on each media volume.

## Install

```bash
mkdir frameforge && cd frameforge
curl -fsSLO https://raw.githubusercontent.com/issaci22/FrameForge/main/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/issaci22/FrameForge/main/.env.example
# edit .env: set MEDIA_PATH, PUID/PGID and TZ (see below)
docker compose up -d
```

Open `http://<host>:8686`. The first visit shows the setup wizard, which creates the admin account.

On Windows, run these in Git Bash or WSL, or use `curl.exe` in PowerShell.

### Image tags

The compose file uses `ghcr.io/issaci22/frameforge:latest`. These tags are published:

| Tag | Meaning |
|---|---|
| `latest` | The newest stable release. Moves with every release. |
| `0.1.0` (`X.Y.Z`) | Exactly one release. Never changes once published. |
| `0.1` (`X.Y`) | The newest patch release of 0.1. |
| `main` | A development build of the newest commit. It can break. Don't use it for your library. |

To stay on one release, pin its tag in `docker-compose.yml`:

```yaml
    image: ghcr.io/issaci22/frameforge:0.1.0
```

Remote nodes must run the **same version** as the server. **Settings → About this server** shows it, and the
snippet from **Nodes → Add node** already uses the matching tag.

### `.env`

| Variable | Default | Meaning |
|---|---|---|
| `MEDIA_PATH` | `./media` | Host folder with your recordings. It appears as `/media` inside the container. |
| `PUID` / `PGID` | `1000` | The user/group that owns your media on the host (run `id` on Linux). FrameForge writes outputs and moves originals as this user. |
| `TZ` | `Etc/UTC` | Time zone for schedules and quiet hours, e.g. `Europe/Berlin` |

That's all. Everything else is configured in the web UI.

### Volumes

| Container path | Holds |
|---|---|
| `/config` | Database (SQLite), logs, node state and finalize journals. Back this up. |
| `/media` (or any path you choose) | Your recordings. Must be **read-write**: FrameForge writes outputs and moves originals here. |

You can mount several media folders, for example `- /mnt/nas/vods:/media/vods` and `- /mnt/nas/raw:/media/raw`, and
add each as a library path.

### Permissions

The container starts as root only long enough to:
- create a user with your `PUID:PGID`
- join the groups that own `/dev/dri/*` and `/dev/nvidia*`
- `chown` **`/config` only** (never your media)

It then drops privileges. If jobs fail with *Permission denied*, `PUID`/`PGID` don't match the owner of your
media. The Libraries page also warns when a library folder isn't writable.

## Settings worth setting early

**Settings → Server URL for nodes.** This is the address nodes on *other* machines use to reach the server, e.g.
`http://192.168.1.20:8686`. If it's empty, the node snippets use the address your browser used. That is
often `localhost`, which doesn't work from another machine. The Add Node wizard warns when that happens.

## Updating

```bash
docker compose pull
docker compose up -d
```

With a pinned tag, change the tag in `docker-compose.yml` first. If you use a GPU overlay, pass it to both
commands (for example `docker compose -f docker-compose.yml -f docker/compose/gpu-nvidia.yml pull`).

Database migrations run automatically at startup. Update remote nodes to the same version at the same time: the
server rejects nodes that speak a different protocol version and shows a clear message.

**Upgrading from an install built from a git checkout:** run `git pull`, then the two commands above from the
checkout. Compose recreates the container from the published image. `/config` is kept, and migrations run as usual.
You can then delete the old local image with `docker image rm frameforge:latest`.

Stopping the container gives running jobs 60 seconds (`stop_grace_period`) to finish moving files. A job
cut off mid-transcode is simply requeued. A job cut off while replacing a file is completed or rolled back when
its node comes back (see [architecture.md](architecture.md#file-safety)).

## Building from source

To run a production container built from your own checkout (for example to test a change), clone the repository
and add the build overlay to every command:

```bash
docker compose -f docker-compose.yml -f docker/compose/build-local.yml up -d --build
```

It builds `frameforge:local` and never pulls. GPU overlays stack after it. For development with live reload, use
`docker-compose.dev.yml` ([architecture.md](architecture.md#development)).

## Backups

Back up `/config`. With SQLite (the default), stop the container first, or copy `frameforge.db` together with
its `-wal` and `-shm` files.

## PostgreSQL (optional)

SQLite is fine for large libraries. To use Postgres, set `FF_DATABASE_URL=postgresql+psycopg://user:pass@host/db`
on the server container.

## Ports

| Port | Purpose |
|---|---|
| 8686 | Web UI, API, and node WebSocket (`/api/v1/node/connect`) |

Nodes only make outbound connections, so a node machine needs no open ports. If you put FrameForge behind a
reverse proxy, allow WebSocket upgrades on `/api/v1/events` (UI live updates) and `/api/v1/node/connect`.
When the proxy serves HTTPS, set `FF_SECURE_COOKIES=true` so the session cookie is marked `Secure`.
