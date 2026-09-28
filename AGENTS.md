# AGENTS.md — Permanent Project Rules for FrameForge

This file holds **long-lived** project context. Read it before changing anything.
Temporary, in-flight state belongs in a local `HANDOVER.md` (git-ignored, never committed), not here.

---

## 1. What FrameForge is

FrameForge is a self-hosted, Docker-first, distributed video transcoding platform built for
**content creators**: YouTubers, streamers, editors, and anyone sitting on terabytes of raw
recordings and VODs.

Core promise: *"Keep my recent footage untouched. After N days, safely compress older
recordings into a storage-efficient format on whatever hardware I have, and never lose a file."*

It is inspired by the *problem space* Tdarr addresses (server/node, queue, rules, FFmpeg). It is an
**independent design**. Never copy Tdarr source, UI, CSS, naming, API shapes, DB schema, config
format or branding.

Litmus questions for every change:
1. Would a YouTuber with 20 TB of recordings actually want this?
2. Can someone who has never used Tdarr understand this screen?
3. Is this actually implemented, or does it only look implemented?

## 2. Architecture (decided, do not casually change)

| Area | Decision |
|---|---|
| Language (backend + node) | Python 3.12, asyncio |
| Server | FastAPI + SQLAlchemy 2 (async) + Alembic |
| Database | SQLite (WAL) by default; Postgres via `FF_DATABASE_URL` (`postgresql+psycopg://`) |
| Node agent | Python asyncio process (`frameforge_node`); in role `all` it runs **in-process** with the server |
| Node ↔ Server | Node opens an **outbound WebSocket** to `/api/v1/node/connect` with a bearer node token. Server pushes assignments; node streams hello, heartbeat, progress, logs and results. |
| File access | **Shared storage.** Nodes mount the same media; per-node *path mappings* translate server paths into node paths. There is no file transfer mode yet, and the UI must not pretend there is. |
| Transcode engine | `TranscodeEngine` abstraction. FFmpeg is the default and the only one implemented. HandBrake is registered but reported unavailable. |
| FFmpeg build | jellyfin-ffmpeg (NVENC/NVDEC, QSV, VAAPI, x264, x265, SVT-AV1, libopus) |
| Image | One image `frameforge`; role chosen via `FF_ROLE` = `all` / `server` / `node` |
| Web UI | React + Vite + TypeScript, TanStack Query, React Router, hand-written CSS tokens. Built **inside Docker only** (no local Node.js required). Served statically by FastAPI. |
| Live updates | UI WebSocket `/api/v1/events` fed by in-process `EventBus`. Do not add polling loops where an event exists. |
| Auth | Local users, argon2id hashes, opaque server-side sessions (HttpOnly cookie). `AuthProvider` seam for future OIDC/API tokens. |

Package layout:

- `shared/frameforge_shared`: code used by both server and node (protocol, media model, quality mapping, FFmpeg command builder, encoder selection, error diagnosis, fingerprints). It must not import server or node code.
- `server/frameforge_server`: API, DB, scanner, rule engine, scheduler, node manager.
- `node/frameforge_node`: capability detection, metrics, job runner, validator, finalizer.
- `web/`: the React app.

## 3. File safety rules (NON-NEGOTIABLE)

- **Never delete or overwrite a source file** until the output has been:
  1. produced by an FFmpeg process that exited 0,
  2. validated (probe, streams, duration tolerance, size sanity), and
  3. moved into its final place and **re-probed successfully**.
- Transcode into `<dest dir>/.frameforge-tmp/`. Never write to the final path directly.
- Every finalize step is journaled (job events + journal file). Finalization must be
  recoverable/rollback-able from filesystem state alone (`finalizer.recover`).
- Never overwrite an existing file that FrameForge did not create. A conflict is a failure, not a guess.
- Preserve the original's mtime/atime on outputs. Age-based rules depend on it.
- Scanners must skip `.frameforge-tmp`, backup dirs and output dirs.
- A failed or cancelled job must leave the source byte-for-byte untouched and clean its temp output.
- **Timed retention ("keep originals for N days") is the only other code that may delete an original**:
  `server/.../services/retention.py`. It exists because users asked for a grace period, and doing it inside the
  finalizer would mean keeping a job open for days. It deletes only when, at deletion time, the output still exists
  with its recorded size and fingerprint, probes cleanly with a video stream, carries `job=<id>;` in its FRAMEFORGE tag,
  matches the original's duration and decodes at start and end; the original still has its recorded size and
  fingerprint, is a regular file inside the recorded root; no job uses either file; and the library still asks for
  timed deletion. Any failed check keeps the file and records why. Entries exist only for completed jobs, and their
  due date is fixed when they're created: shortening the period never deletes anything sooner by itself.

## 4. FFmpeg rules

- All argument construction lives in `frameforge_shared/ffmpeg_builder.py` (jobs, compression previews, decode
  checks). Never build FFmpeg strings ad-hoc elsewhere. Always pass args as a list (no shell).
- What containers and codecs can do together lives in `frameforge_shared/compat.py`. It advises (and blocks saving
  truly impossible profiles) at the API boundary only; stored profiles and job snapshots are never rejected on load.
- Quality is a friendly 0–100 slider mapped **per encoder** in `quality.py`. Never claim the
  slider means the same thing across codecs. Always show the native value (e.g. "CRF 22").
- Preserve metadata, chapters, subtitles, attachments and color/HDR tags when the destination
  container/codec can hold them. Drop incompatible items explicitly and **record a note** on the job.
- Never upscale resolution or frame rate. Resolution caps apply to the **short side** so vertical video
  isn't crushed.
- Progress comes from `-progress pipe:1`. Stderr is the job log.
- New failure signatures go in `errors.py` as friendly diagnoses (title, explanation, likely causes).

## 5. GPU rules

- **No hardcoded vendor assumptions.** Support is decided per node by *verified* capability detection
  (a real short test encode/decode: a few frames on the actual device), not by the presence of an encoder name in `ffmpeg -encoders`.
- Backend preference for `auto`: NVENC → QSV → VAAPI → AMF → CPU (only among verified).
- Hardware decode is optional and must fall back to software decode automatically on failure.
- UI must only show acceleration as available when the node verified it. Metrics that can't be read
  (e.g. Intel GPU utilization without extra tooling) are shown as "not reported", never faked.
- AMD on Linux = VAAPI. AMF is detected if present but not expected in Linux containers.
- Multi-GPU: a node uses one VA-API/QSV device (`render_device`, preferring Intel). To use several GPUs at once,
  run one node container per GPU.
- **Pinned GPU devices keep their host name inside the container.** When a single device is passed through, map it
  to the same path: `/dev/dri/renderD129:/dev/dri/renderD129`, never `renderD129:renderD128`. The node identifies
  the vendor (and so which backends to test, e.g. QSV only on Intel) from the host's `/sys/class/drm/<renderD name>`,
  which the container sees unchanged. A renamed device is read as whichever GPU owns that name on the host, so the
  node reports the wrong vendor and tests the wrong backends.

## 6. Coding standards

- Python: type hints everywhere, pydantic v2 models at boundaries, `async` for I/O. Blocking work
  (filesystem walks, hashing) goes through `asyncio.to_thread`.
- Keep modules focused. No giant files; split before a module passes ~600 lines.
- No magic values. Put constants in the module that owns them or in settings.
- Logging goes through `logging.getLogger(__name__)`. No `print`.
- TypeScript: strict mode. API types live in `web/src/api/types.ts` and mirror the server schemas.
- No fake features. A button either works or is visibly disabled with a reason.
- No TODO-driven architecture. If something is deferred, record it in docs (or the local HANDOVER.md), not as dead UI.

## 7. Database rules

- All schema changes go through **Alembic migrations** (`server/frameforge_server/db/migrations`).
  Never edit an already-released migration; add a new one.
- Migrations run automatically at server start.
- JSON columns are allowed only for **validated structured documents** (profile spec, rule
  condition tree, capabilities, metrics snapshots), never as a dumping ground.
- Keep SQLite and Postgres compatible (no SQLite-only SQL in services).

## 8. Security rules

- **No default credentials, ever.** On first run (no users in the DB) `/api/v1/auth/status` reports `setup_required`
  and the UI sends every route to the `/setup` wizard, which creates the first admin. `POST /auth/setup` returns
  409 once any user exists. Don't add default logins or env-var passwords, and don't remove or bypass the login screen.
- Passwords: argon2id only. Session and node tokens are stored as **SHA-256 hashes**. The plaintext is shown once.
- Unsafe HTTP methods require the `X-FrameForge-Client` header (CSRF defense with SameSite=Lax cookies).
- Never send secrets, token hashes or password hashes to the frontend.
- Node tokens authenticate node sockets only; they can't call user APIs.
- Validate every path from users against configured library roots before acting on it.

## 9. Docker rules

- `docker compose up -d` must work with the default compose file and no extra config.
- Production pulls `ghcr.io/issaci22/frameforge` (published by `.github/workflows/docker.yml`); development builds locally (`docker-compose.dev.yml`, or `docker/compose/build-local.yml` for a production-style build).
- Keep env vars minimal (`FF_ROLE`, `FF_PORT`, `PUID`, `PGID`, `TZ`, `FF_SERVER_URL`, `FF_NODE_TOKEN`).
  Everything else is configured in the UI.
- Container runs as `PUID:PGID` (entrypoint drops privileges and joins `/dev/dri` groups).
- Persistent state lives only in `/config` (DB, logs, node state).
- GPU examples live in `docker/compose/` and docs/gpu.md. Be honest about host requirements.
- Every example that passes a single `/dev/dri/renderD*` device maps it to the same name inside the container (§5).

## 10. UI principles

- Look and feel: a calm, approachable homelab app (dark by default, amber `#f0a23b` accent, three-bar logo mark).
  Changed 2026-09-26 from "dense": the owner found the dense UI intimidating. Keep hierarchy clear and complexity
  progressive: a new user must not be overwhelmed, and an advanced user must still reach every option.
- One surface per section: a `Section` (`.panel`) with a sentence-case title and a short description. No boxes in boxes,
  no uppercase micro-labels. Surfaces differ by background, not heavy borders. Shadows only on overlays.
- Use the shared kit in `web/src/components/ui/` (re-exported by `components/ui.tsx`): `PageHeader`, `Section`,
  `SettingRow` (label + description left, control right), `Menu` (⋯ overflow for secondary and destructive actions),
  `ConfirmButton` (anchored confirmation popover), `Tooltip`/`InfoTip`, `LoadingState`/`ErrorState`/`Empty`, `Tabs`
  (`variant="pills"` for filters). Don't hand-roll page headers, panels or confirm flows.
- Styles: tokens in `styles/tokens.css`; `base.css` (utilities), `shell.css`, `controls.css`, `surfaces.css`,
  `overlays.css`, `pages.css`, plus `setup.css` (wizard flow), `setup-steps.css` (controls inside its steps) and
  `transcode.css`. Use utility classes instead of inline `style` except for
  computed values (widths, progress).
- Monospace for numbers, sizes and timecodes. Color carries meaning (state, codec), sparingly.
- Every page has a loading state, an error state with retry, and an empty state that names the next step.
- Destructive actions ask first (`ConfirmButton` or a `Menu` item with `confirm`) and say what is and isn't touched.
- Escape closes only the top-most overlay (`useEscape` stack). Works at phone width (off-canvas nav, bottom-sheet
  modals, no horizontal page scroll).
- Avoid: random gradients, glassmorphism, marketing-size cards, giant meaningless numbers, flashy animation.
- Errors are explained in human language with likely causes. Raw logs sit behind a disclosure.
- Everything shown must reflect real server state.

## 11. Testing philosophy

- **Build first, test at milestones.** Do not write exhaustive tests before features exist.
- Priority test targets: rule engine, quality mapping, FFmpeg builder, validator, finalizer
  (safety!), and scheduler eligibility. Tests live in `tests/` and run with
  `docker compose -f docker-compose.dev.yml run --rm dev pytest`.
- The finalizer and anything that touches source files must have tests before being changed.

## 12. Git / workflow

- Small, focused commits with imperative messages.
- Update your local `HANDOVER.md` when the active task changes significantly.
- Update this file when a permanent decision changes, and say why.

## 13. Backwards compatibility

- The API is versioned under `/api/v1`. Do not break it; add fields rather than renaming them.
- The node ↔ server protocol carries a `protocol` version in `hello`. The server must reject
  incompatible nodes with a clear message instead of misbehaving.
- Profile specs and rule trees are versioned documents. Add fields with defaults.

## 14. Things future agents must NOT do

- Do not copy Tdarr (code, UI, names, schema, config format).
- Do not hardcode NVIDIA (or any vendor) paths into core logic.
- Do not delete/overwrite source media outside the finalizer and `services/retention.py` (see §3).
- Do not add UI for features that don't exist.
- Do not store application state in ad-hoc JSON files (DB only; node journal files are the one documented exception).
- Do not require users to hand-edit dozens of env vars.
- Do not install Node.js on the host. The web UI builds in Docker.
- Do not work outside the repository directory without permission.

## 15. Important implementation decisions (keep these true)

- **Job state machine:** `queued → assigned → preparing → transcoding → validating → finalizing → completed | failed | cancelled`.
  Only the scheduler moves `queued → assigned`. Only node messages move a job through the active states.
- **The scheduler commits before it sends an assignment.** The built-in node answers within milliseconds; if the
  commit comes after the send, the first stage message races the commit and gets dropped.
- **Nodes buffer unreported results.** `hello.active_jobs` includes finished-but-unreported job ids, so the server
  doesn't requeue a job whose file was already replaced. Requeueing it would re-encode an encode.
- **Finalizing jobs are pinned to their node.** They are never requeued elsewhere. The server sends `job.recover`
  when that node reconnects.
- **Re-processing guard:** a file is skipped when `processed_profile_id == rule.profile_id`, when its FRAMEFORGE
  tag contains `profile=<id>;`, or when its fingerprint is in `processed_fingerprints`. Aging stages that use
  different profiles (e.g. H.265 → later AV1) still apply, by design.
- **Outputs inherit the original's mtime/atime** (`os.utime` before finalize). Without this, age rules reset.
- **Timestamps:** the DB stores naive UTC via the `UTCDateTime` type, and Python always sees aware UTC.
  Schedules and quiet hours use container local time (`TZ`).
- **Utilization limits** (`max_gpu_util` / `max_cpu_util`) apply only while the node has no FrameForge jobs.
  Otherwise our own jobs would block us.
- **Live updates:** the server EventBus feeds the `/api/v1/events` WebSocket. The UI keeps high-frequency data
  (progress, metrics, logs, scans) in `web/src/live/events.ts` and invalidates React Query caches for structural changes.
- **UI WebSocket auth** uses the session cookie. Node WebSocket auth uses `Authorization: Bearer ffn_…`.
- **Default port:** 8686. The built-in node is named `<container hostname> (built-in)` (or `FF_NODE_NAME`).
- **Node features:** new abilities are advertised in `Hello.features` (`preview.v1`, `audio.v2`, `result.original.v1`)
  instead of bumping `PROTOCOL_VERSION`. The server never sends a node work that needs a feature it didn't list
  (the scheduler and the preview service both check), so older nodes keep working.
- **Storage policy is two questions per library:** `output_location` (next to the original / separate folder) and
  `original_handling` (keep / delete after success / keep N days), plus `kept_original_location` (backup folder or in
  place). The legacy `output_policy` is derived and still accepted from old clients; it never deletes more than the
  new fields. Mapping lives in `services/storage_policy.py` and, frozen, in migration 0003.
- **Kept originals don't get converted again:** an original that stays in the library after conversion gets
  `media_files.role = "kept_original"`, and rules skip it (manual queueing still works).
- **Compression previews run on nodes**, one at a time, and take a job slot while running (GPU session limits). Images
  travel to the server in chunks over the node socket and live in a disposable cache (`config/previews`).
- **Built-in profiles are identified by `builtin_key`, never by name.** The first lineup (YouTube Archive, Storage
  Saver, Long-Term Archive) was upgraded in place once, and only where the user hadn't edited it.

## 16. Development environment notes

- Host tools: Docker, git, and Python 3.12 for the optional host venv. No Node.js. Build and typecheck the web UI in a
  `node:22` container.
- On Windows, **never use PowerShell `Get-Content`/`Set-Content` round trips** to edit files: PS 5.1 mangles UTF-8
  (typographic quotes, arrows, ellipses). Use an editor or Python.
- In Git Bash, prefix `docker run`/`docker exec` commands that contain container paths with `MSYS_NO_PATHCONV=1`.
- `@dataclass` objects stored in sets or used as dict keys need `eq=False`, because default dataclasses are unhashable.
- Test media: `scripts/make-test-media.sh`. Files created as root must be made writable for PUID 1000.
- UI screenshots: the `mcr.microsoft.com/playwright/python:v1.49.0-noble` image, pointed at `http://host.docker.internal:8686`.
- Checking UI changes without rebuilding the image: run Vite in a throwaway `node:22-bookworm-slim` container
  (`npx vite --host 0.0.0.0`, `VITE_API_TARGET=http://host.docker.internal:<port>`), and run Playwright with
  `--network container:<vite container>` against `http://localhost:5173` (Vite rejects the `host.docker.internal` Host
  header). File changes don't reach Vite's watcher through the Windows bind mount: restart the Vite container before each
  screenshot run. For a backend with known credentials, start a separate container from `frameforge:dev` on another port
  with its own scratch `/config` and `/media` (chown the media to 1000:1000), never the real `.dev-config`.
- UI automation must never loop on a state-changing click. Retrying an old failed job re-queues its file every time,
  even after it was converted (manual jobs skip the re-processing guard).
