# Architecture

For contributors. The permanent design rules are in [AGENTS.md](../AGENTS.md); this document explains how the pieces fit.

## Overview

```
                ┌──────────────────────── server (FF_ROLE=server|all) ────────────────────────┐
 browser ──HTTP─┤ FastAPI /api/v1 ── services: scanner · rule engine · scheduler · node manager │
         ◄──WS──┤ /api/v1/events ◄── EventBus ◄──────────────┘                                │
                │ SQLite/Postgres (SQLAlchemy 2 async, Alembic)                                 │
                └───────────────▲────────────────────────────────────────────▲────────────────┘
                                │ WS /api/v1/node/connect (Bearer ffn_…)      │ same protocol, in-process
                        ┌───────┴────────┐                           ┌────────┴───────┐
                        │ remote node    │   … more nodes            │ built-in node  │ (FF_ROLE=all)
                        │ FF_ROLE=node   │                           └────────────────┘
                        └───────┬────────┘
                                │ shared storage (same media, optional path mappings)
```

- **One image, three roles.** `FF_ROLE=all` runs the server and a node in one process. The built-in node still
  talks to the server over the same WebSocket protocol, so there's one code path for local and remote work.
- **Nodes connect out.** A node dials the server, authenticates with a bearer token, and receives pushed assignments.
  Nodes never poll, and nothing connects to a node.
- **Shared storage.** Nodes read sources and write outputs directly on the media share. Path mappings translate server
  paths into node paths. Files are never streamed over the WebSocket.

## Packages

| Package | Contents | Imports |
|---|---|---|
| `shared/frameforge_shared` | Protocol models, media model and ffprobe parsing, profile spec, quality mapping, **FFmpeg command builder**, encoder selection, error diagnosis, fingerprints, path mapping | Nothing from server or node |
| `server/frameforge_server` | API routers, auth, DB models and migrations, scanner, rule engine, scheduler, node manager, stats, EventBus | shared |
| `node/frameforge_node` | Agent (WebSocket client), capability detection, metrics, job runner, validator, finalizer | shared |
| `web/` | React + Vite + TypeScript UI, built in Docker and served statically by FastAPI | — |

## A file's journey

1. **Scan** (`services/scanner.py`)
   - Walk library folders in a thread, skipping hidden, temp, backup and output folders.
   - New or changed files are fingerprinted (xxh3 of the size plus 3×1 MiB samples) and probed with ffprobe.
   - A file whose fingerprint matches a *missing* file is treated as a move and keeps its history.
   - A fingerprint that matches a known FrameForge output marks the file as processed.
2. **Decide** (`services/rules/engine.py`): `decide()` walks enabled rules by position, evaluates each condition tree
   against a `FileContext`, and applies the guards ([rules.md](rules.md#guards-that-override-rules)). A *transcode*
   decision creates a `Job` holding a **snapshot** of the profile spec. Editing a profile later doesn't change queued jobs.
3. **Schedule** (`services/scheduler.py`)
   - Filter nodes (`_node_block`) and job windows (`_job_window_block`), pick an encoder per node (`select_encoder`),
     and score the candidates (GPU first, then free slots, then CPU load).
   - Build the `FinalizePlan` and **commit the `assigned` state before sending** `job.assign`.
4. **Run** (`node/runner.py`)
   - Map paths, check that the source matches the scan (size), check free space, run the finalizer's **preflight**
     (refuse a plan that can never finish, such as an existing file at the output path, *before* encoding), probe,
     and build the command (`ffmpeg_builder.build_command`).
   - Run FFmpeg with `-progress pipe:1`, streaming progress and log lines. If a decode-related failure occurs,
     rebuild the command with software decoding and try once more.
   - Copy the original's atime/mtime and mode onto the output.
5. **Validate** (`node/validator.py`): output exists and has a sane size, ffprobe reads it, video (and audio) streams are
   present, the duration is within tolerance, a size ratio is checked, and the first and last 5 s decode cleanly.
6. **Finalize** (`node/finalizer.py`): see below.
7. **Report:** `job.result` goes back to the server. If the socket is down, the node buffers the result and lists the
   job in `hello.active_jobs` after reconnecting. The server records the output's fingerprint and FRAMEFORGE tag, so
   the file is recognized as processed from then on. The result also says where the original ended up (deleted, backed
   up to which exact path, kept, or rescued) with its size and fingerprint.
8. **Retain** (`services/retention.py`), only for libraries that keep originals for a period: a `retained_originals`
   row records both files. When it's due, the server re-checks everything and deletes the original, or holds it back
   with a reason ([libraries.md](libraries.md#keeping-originals-for-a-while)).

## Job state machine

```
queued ─► assigned ─► preparing ─► transcoding ─► validating ─► finalizing ─► completed
   ▲          │            │             │              │            │
   └──────────┴────────────┴─────────────┴──────────────┘            ├─► failed
        requeue (node lost, declined, transient failure)             └─► (pinned to its node until recovered)
                                          any active state ─► failed | cancelled
```

- Only the scheduler moves a job from `queued` to `assigned`. Only node messages move it through the active states.
- A job in `finalizing` is never requeued elsewhere. When its node reconnects, the server sends `job.recover`.
- A job that is `assigned` but not acknowledged within 90 s is requeued. A node offline for more than 120 s has its non-finalizing
  jobs requeued, and an attempt is counted.

## File safety

**Transcode.** Output goes to `<final dir>/.frameforge-tmp/job-<id>.<ext>`, never to the final path. A failed or
cancelled job deletes its temp output. The source is only ever *read*.

**Finalize.** It only runs once validation has passed. Every step first writes a journal entry to
`job-<id>.<ext>.journal` next to the temp output:

| Step (journal) | In-place (final path = source path) | New path (different extension or folder) |
|---|---|---|
| `holding_original` | rename source → `…/.frameforge-tmp/job-N.ext.original` | — |
| `placing_output` | rename temp → final | hard-link temp → final (atomic, never overwrites), then unlink temp; rename as a fallback |
| `verifying` | check the size matches, then re-probe and require this job's FRAMEFORGE tag. **On failure, roll back**: output back to temp, original restored. | same |
| `backing_up_original` / `deleting_original` | move the parked original to the backup folder (a cross-disk move is copied and size-verified first) or delete it | same, for the source |

- Anything that fails *after* verification only produces a warning, and the original is kept: it stays in place, or is renamed
  `name.original.ext` when the final file now has its name.
- Existing files FrameForge didn't create are never overwritten. Backups get `(1)`, `(2)`, … suffixes.

**Recovery** (`finalizer.recover`) needs only the plan and the filesystem. It inspects which of *temp*, *final* and
*parked original* exist, then either resumes (verify, then handle the original) or rolls back (put the original back).
It verifies with the job's FRAMEFORGE tag, so an untouched original can never be mistaken for a finished output. The
tests in `tests/test_finalizer.py` kill the finalizer at every journal step for every output policy, run `recover()`,
and assert the original always survives. The backup destination is journaled before the move, so a recovered job can
still report where the original went; without a record it reports *unknown* rather than guessing.

**Timed retention** is the only other code allowed to delete an original. It re-verifies the output (size,
fingerprint, ffprobe, video stream, FRAMEFORGE tag, duration against the original, start/end decode) and the original
(size, fingerprint, regular file inside the recorded root), refuses while any job uses either file, and runs under a
lock shared with *Delete now*. Due dates are fixed when a job completes; a shorter period never deletes anything
sooner by itself.

**Compression previews** never touch libraries. The node encodes short samples into its own state folder with the
same video arguments as a job (`ffmpeg_builder.build_preview_commands`), grabs PNG frames of the original and the
sample at the same moment, and sends them to the server in chunks over the socket. The server keeps them in memory
plus `config/previews` (wiped at startup, expired after a few hours). A running preview holds one of the node's job
slots.

## Node ↔ server protocol

Nodes list what they can do in `hello.features` (`preview.v1`, `audio.v2`, `result.original.v1`). New abilities are
added this way instead of bumping `PROTOCOL_VERSION`, and the server only sends a node work whose features it listed.
Previews add `preview.request` / `preview.cancel` (server → node) and `preview.progress` / `preview.chunk` /
`preview.result` (node → server).

Frames are `{"type": str, "data": {...}}` JSON, validated with the pydantic models in `frameforge_shared/protocol.py`.

| Direction | Messages |
|---|---|
| node → server | `hello` (protocol version, capabilities, active and unreported job ids, features), `heartbeat` (metrics every 2 s), `job.stage`, `job.progress`, `job.log`, `job.result`, `node.log`, `capabilities` |
| server → node | `welcome` (id, name, concurrency, path mappings), `job.assign`, `job.cancel`, `job.recover`, `config`, `capabilities.redetect`, `error` |

Adding optional fields is backwards compatible. Anything else bumps `PROTOCOL_VERSION`. The server refuses nodes with a
different version, with a readable error.

## Capability detection

For every FFmpeg encoder FrameForge knows, the node runs a real 5-frame encode of a synthetic source on the relevant
device, and records `verified` plus the error. For hardware decoding, it decodes small sample files per codec. Only
verified entries are used by `select_encoder` or shown as available. `python -m frameforge_node --detect` prints the
same report.

## Live updates

Services publish to the in-process `EventBus` (`events.py`). The `/api/v1/events` WebSocket fans the events out to browsers.
The UI keeps high-frequency data (progress, metrics, log lines, scan progress) in a small store (`web/src/live/events.ts`) and
invalidates TanStack Query caches on structural events (job created/updated, node status). Don't add polling where an event exists.

## Data

- SQLite in WAL mode by default, or Postgres via `FF_DATABASE_URL`. All schema changes are Alembic migrations, applied at startup.
- The DB stores naive UTC (`UTCDateTime`), and Python always sees aware UTC. Schedules use container local time (`TZ`).
- JSON columns only hold validated documents: profile specs, rule condition trees, capabilities, metrics snapshots,
  finalize plans.

## Security

- Passwords use argon2id. Sessions are opaque random tokens in an HttpOnly, SameSite=Lax cookie. Only their SHA-256 hash is stored.
- Unsafe HTTP methods require the `X-FrameForge-Client` header (CSRF defense).
- Node tokens (`ffn_…`) are stored hashed, shown once, and only valid on the node socket.
- Paths that come from users are checked against library roots before any action.

## Development

Everything runs in Docker. The host needs Docker, and optionally Python 3.12 for running tests without the container.

```bash
docker compose -f docker-compose.dev.yml up --build dev web   # API :8686, Vite hot reload :5173
docker compose -f docker-compose.dev.yml run --rm dev pytest -q
```

Python source is bind-mounted, so restart `dev` after backend changes. Rebuild the image only when the web UI or
dependencies change. `scripts/make-test-media.sh` generates sample recordings.

### Tests

The suite lives in `tests/` and focuses on the parts where a bug costs data or trust:

| File | Covers |
|---|---|
| `test_finalizer.py` | Every output policy, refusals, rollback, crash injection at each journal step + recovery |
| `test_finalize_plan.py` | Where temp, output and backup paths go for each storage policy; old `output_policy` values plan exactly as before |
| `test_runner.py` | Preflight refuses before encoding; results report where the original went (real FFmpeg) |
| `test_retention.py` | Every retention check blocks deletion on its own; re-linking, moved outputs, paused libraries, period changes |
| `test_compat.py` | Container/codec/audio advice, hardware-aware warnings, node feature requirements, quality bands |
| `test_presets_upgrade.py` | Built-in lineup: fresh seeding and the one-time upgrade (unedited, edited, deleted, name taken) |
| `test_previews.py` | Preview round trip node → server with real FFmpeg; cancel, busy, timeout, disconnect, bad file names |
| `test_migrations.py` | Data migrations, including the storage-policy split backfill and its downgrade |
| `test_validator.py` | Every validation check (mocked probe), plus real FFmpeg round trips |
| `test_ffmpeg_builder.py` | Argument lists: caps, no upscaling, streams, notes, HDR, each GPU backend |
| `test_encoder_selection.py` | Only verified encoders, preference order, hardware-decode decisions |
| `test_quality.py` | Slider mapping per encoder: monotonic, in range, labels |
| `test_rules_engine.py` | Operators, trees, traces, `decide()` guards, aging stages and boundaries |
| `test_scheduler.py` | Time windows, quiet hours, node blocks, placement, commit-before-send (real SQLite) |
| `test_scanner.py` | Temp, backup and output folders are never scanned (POSIX only, runs in the container) |
| `test_pathmap.py` | Server → node path translation |

Tests that need `ffmpeg` skip themselves when it isn't installed. The finalizer and anything else that touches source
files must have tests before it is changed.
