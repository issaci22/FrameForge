# First-Time Setup Guide

This guide takes a fresh FrameForge install to a working, automated setup: a secured admin account, libraries for
your footage, tested profiles, an aging policy, and nodes with verified hardware. It assumes the container is
already running ([installation.md](installation.md)). Each section links to the reference page with the full details.

**Recommended order:** secure the server → add libraries with automation **off** → test profiles on a few files →
build rules → turn automation on.

---

## 0. Before you open the browser

| Check | Why it matters | Where |
|---|---|---|
| `PUID`/`PGID` match the owner of your media (`id` on the host) | FrameForge writes outputs and moves originals as this user. A mismatch makes every job fail with *Permission denied*. | `.env` |
| `TZ` is your time zone, e.g. `Europe/Berlin` | Quiet hours, rule windows and node working hours all use container local time | `.env` |
| Media is mounted **read-write** | Outputs, temp files and backups are written next to your recordings | `docker-compose.yml` |
| `/config` is on persistent storage | It holds the database, logs and node state. Back it up. | `docker-compose.yml` |
| GPU overlay included (if you have a GPU) | Without it, only CPU encoders exist inside the container | [§5.2](#52-run-capability-detection) |

To keep VODs, raw recordings and archives apart, mount each one separately. Replace the single `/media` volume:

```yaml
    volumes:
      - ./config:/config
      - /mnt/nas/vods:/media/vods          # stream VODs
      - /mnt/nas/raw:/media/raw            # OBS / camera recordings
      - /mnt/archive:/media/archive        # long-term storage
```

---

## 1. Initial access & security

### 1.1 The setup wizard

FrameForge has **no default login**. While the database has no users, every page redirects to `/setup`.

The wizard has eight steps. **Only the Administrator step writes anything right away**, because every other API
needs the session that account creates. Everything after it is a draft, applied together by **Complete setup**.

| Step | You set | Notes |
|---|---|---|
| **1. Welcome** | – | What the wizard configures |
| **2. Administrator** | Username (pre-filled `admin`), password, confirmation | Creates the account and signs you in. The later steps stay locked until then. |
| **3. Storage** | Libraries: name, folders *inside the container* (default `/media`), scan interval, output policy, backup/output folder, safety checks, exclude patterns | Each folder is checked against the container's filesystem as you type. A missing folder blocks **Continue**. |
| **4. Transcoding** | The archive profile (default Balanced), its quality, format, audio, and advanced settings (same controls as the Profiles page) | Shows the native value per encoder (e.g. *libx265 CRF 24*, *hevc_nvenc CQ 26*) and whether a node verified each encoder. Changes are saved to that profile. |
| **5. Rules** | The aging policy (default: keep 0–30 days, then the archive profile), optional rules checked before it, and **Observe first** (default) or **Queue jobs automatically** | *Observe first* creates the libraries with *Create jobs automatically when rules match* off |
| **6. Nodes** | Server URL for nodes, path mappings for remote nodes | Shows each node's verified encoders and the `--detect` commands. **Add remote node…** creates a node immediately (its token is shown once). |
| **7. Schedule** | Background work hours, an aging-policy window, per-node jobs at once, utilization limits and working hours | A weekly grid shows when each kind of job may start |
| **8. Review** | – | Lists problems per section, then **Complete setup** runs each API call in order and shows its result. When everything is applied, a summary says what happens next (first scans, whether jobs are queued) and lists any warnings; **Open dashboard** takes you in. |

If a call fails, Complete Setup stops there and shows the server's message next to it. Fix it and run it again:
libraries and rules that were already created aren't created twice. The draft is kept in the browser tab
(never the password), so a reload resumes where you were.

> **Warning:** **Queue jobs automatically** queues every file that is already past a stage's age as soon as the
> first scan finishes. On a library with years of footage, that can be thousands of jobs. Keep **Observe first** if
> you want to test profiles first ([§3.6](#36-test-a-profile-before-automating)), then turn on *Create jobs
> automatically when rules match* per library.

Once an account exists, the setup endpoint returns *409 Setup has already been completed*, so nobody can re-run
the wizard to create a second admin.

### 1.2 The admin account

- **Username:** 2–64 characters: letters, digits, `_`, `.`, `-`.
- **Password:** at least 8 characters. It's hashed with **argon2id** and never stored in plain text. Hashes are
  upgraded automatically on sign-in if the hashing parameters change.
- **Change it:** **Settings → Account → Change password**. This signs out every other session.

### 1.3 Sessions

| Property | Behavior |
|---|---|
| Cookie | `ff_session`, HttpOnly, SameSite=Lax |
| Lifetime | 30 days (set `FF_SESSION_DAYS` to change it). The server also expires sessions after 30 days without use and purges them automatically. |
| Server storage | Only a **SHA-256 hash** of the session token is stored, so a copy of the database contains no usable cookies |
| Sign out | Deletes the session on the server, not just the cookie |
| Brute-force protection | 10 failed sign-ins from one IP within 5 minutes → *Too many failed sign-in attempts* (HTTP 429) until the window passes |
| CSRF protection | Unsafe API calls must send an `X-FrameForge-Client` header. The web UI does this. Add it yourself when scripting against the API (`/api/docs`). |

### 1.4 Access from outside your network

- FrameForge has no two-factor sign-in. For remote access, prefer a VPN (WireGuard, Tailscale) over exposing port 8686.
- Behind a reverse proxy, allow WebSocket upgrades on `/api/v1/events` (live UI updates) and
  `/api/v1/node/connect` (nodes).
- When the proxy serves HTTPS, set `FF_SECURE_COOKIES=true` so the session cookie is marked `Secure`.

### 1.5 User access: what exists today

| Feature | Status |
|---|---|
| Single admin account (created by the wizard) | ✅ |
| Password change, sign-out, session expiry | ✅ |
| Additional users, roles, read-only accounts | ❌ Not built. There is no page or API to add users. |
| OIDC / SSO, API tokens | ❌ Not built. The auth layer has a provider seam for them. |
| Password reset | ❌ None. Keep the password in a password manager and back up `/config`. |

**Node tokens (`ffn_…`) are separate credentials.** They can only open a node connection. They can't sign in or
call the API. Each token is shown once, and the server stores only its SHA-256 hash.

### 1.6 General settings to set now

**Settings → General:**

| Setting | Default | Set it to |
|---|---|---|
| **Server URL for nodes** | empty | The address other machines use, e.g. `http://192.168.1.20:8686`. If it's empty, node snippets use your browser's address, often `localhost`, which remote nodes can't reach. |
| Automatic retries | 2 attempts | Leave it. Applies only to temporary failures (busy GPU encoder, out of memory, node lost). |
| Parallel file analysis | 4 | Lower it on slow network shares; raise it on fast local disks |
| Keep job history for | 180 days | As you like |

Background work hours (quiet hours) are covered in [§5.5](#55-scheduling-priorities-and-time-windows).

---

## 2. Connecting storage & media libraries

### 2.1 Container paths, not host paths

Library folders are paths **inside the container**. With `/mnt/nas/vods:/media/vods` mounted, the library folder is
`/media/vods`. The folder picker (🔍) only browses what the container can see.

### 2.2 Plan your libraries

Where converted files go and what happens to originals are set **per library**, so split your folders by how
their originals should be treated:

| Library | Folder | Converted files / originals | Why |
|---|---|---|---|
| Stream VODs | `/media/vods` | Next to the original · keep for 14 days | Large H.264 files you rarely re-edit; two weeks to spot a bad profile |
| Raw recordings | `/media/raw` | Next to the original · keep (backup folder) | Untouched while you edit, compressed later |
| Archive | `/media/archive` | Next to the original · delete after success | Maximum savings, once you trust your profiles |
| Exports / uploads | `/media/exports` | In a separate folder · keep | Originals must never change |

- **The library decides where the output goes, not the profile.** If you queue *YouTube Upload* on a file in a
  library that moves originals to a backup folder, the upload version takes that file's place and the original goes
  to the backup folder.
- **Don't let library folders overlap.** Each library tracks its own files, so a shared folder is scanned and
  evaluated twice.

### 2.3 Add a library

**Libraries → Add library:**

| Field | Default | Notes |
|---|---|---|
| Name | – | e.g. *YouTube VODs* |
| Scan every | 60 minutes | Minimum 5. New files are only picked up on scans. |
| Folders | – | Container paths. Sub-folders are included. **Add folder** adds more. |
| Where do converted files go? / What happens to the original? | Next to the original · keep it (backup folder) | See [§2.4](#24-file-policies) |
| Keep originals for / While it's kept | – | Shown for the choices that use them |
| Backup folder / Output folder | – | Shown for the choices that use one |
| Library enabled | on | |
| **Create jobs automatically when rules match** | on | **Off** still scans and evaluates rules but queues nothing. Use it for your first days, to see what rules *would* do. |
| Advanced → Exclude patterns | none | One per line, shell-style, matched against the file name *and* the path relative to the library: `replay-*`, `clips/*`, `*.part.mkv` |
| Advanced → safety checks | see [§2.5](#25-safety--validation-settings) | |

**Add & scan** saves the library and starts the first scan.

- **Scanned extensions:** `.mkv .mp4 .mov .m4v .flv .ts .m2ts .mts .avi .webm .wmv .mpg .mpeg .mxf`
- **Always skipped:** hidden files and folders (including `.frameforge-tmp` and `.frameforge-originals`), the
  library's own output and backup folders, and anything matching an exclude pattern.
- If the Libraries page warns that a folder **isn't writable**, fix `PUID`/`PGID` or the mount before you turn automation on.

### 2.4 File policies

Two questions, answered per library ([libraries.md](libraries.md#output-and-originals)):

| Where do converted files go? | What happens to the original? | New file | Original | Space freed |
|---|---|---|---|---|
| Next to the original | **Keep it** → move to a backup folder (default) | The original's name, new extension | In the backup folder (sub-folders mirrored) | Only once backups are deleted |
| Next to the original | **Keep it** → leave it where it is | `name.ff.mp4` beside it | Untouched | None |
| Next to the original | **Keep it for a while** (1–3650 days) | As above | Deleted after the period, once the new file passes its checks again | After the period |
| Next to the original | **Delete after success** | The original's name | Deleted after the new file is validated, placed and re-probed | Immediately |
| In a separate folder | Keep / keep for a while / delete | The output folder (sub-folders mirrored) | Untouched / deleted later / deleted right away | None / later / immediately |

The editor repeats your choice as one plain sentence before you save.

**Backups:**
- The default backup folder is a hidden `.frameforge-originals` inside the library folder. That's the **same disk**,
  so no space is freed until backups are deleted, by you or by *Keep it for a while*.
- To free space on the media disk right away, pick a backup folder on another disk. Cross-disk moves are copied
  and verified before the original is removed.

**Keeping originals for a while:** the server deletes each original after the period only if the new file still
exists unchanged, probes and decodes cleanly, carries this job's FRAMEFORGE tag and matches the original's duration,
and the original is still the file that was converted. Anything else holds it back, with the reason shown on the
library. Shortening the period never deletes anything sooner by itself.

**Conflicts:**
- FrameForge never overwrites a file it didn't create. If `stream.mkv` already exists when `stream.mp4` is
  converted to MKV, the job fails *before encoding* with *A file already exists at the output location*.
- A backup whose name is taken gets a suffix: `stream (1).mkv`.
- If the original can't be moved or deleted, you get a warning and the original stays. With in-place replacement it's kept as `stream.original.mkv`.

> **Aging policies and kept originals:** an original that stays in the library next to its output is marked as a kept
> original, so a later aging stage converts the output instead of the original. With a separate output folder and
> *Keep it* (forever), originals stay regular sources on purpose (for several outputs per source), so a later stage
> can hit an existing output name and stops before encoding.

**How a finished encode replaces a file:**
1. FFmpeg writes into `<destination folder>/.frameforge-tmp/`, never to the final path.
2. The output is validated ([§2.5](#25-safety--validation-settings)). If validation fails, the temp file is deleted
   and the source is left byte-for-byte untouched.
3. The original's **mtime/atime**, permissions and ownership are copied onto the output.
4. The output is moved into place. Each step is recorded in a journal next to the temp file (`.frameforge-tmp/job-N.mkv.journal`).
5. The output is **re-probed at its final path**.
6. Only then is the original backed up or deleted.

If the container or node dies at any step, the job is rolled forward or back from the journal when the node reconnects.

### 2.5 Safety & validation settings

**Libraries → edit → Advanced: exclusions & safety checks**

| Setting | Default | Meaning |
|---|---|---|
| Max duration difference | 2 % | The output's length must be this close to the source's |
| Minimum output size | 1 MB | Smaller outputs are treated as broken |
| Size limit | 100 % of original | Output size relative to the source. Empty disables the check (minimum 10 %). |
| **Reject outputs over the size limit and keep the original** | **on** | On: the job fails and the original stays. Off: the larger output is used anyway, with a warning. |
| Require audio when the original has audio | on | Catches encodes that silently lost their audio |

These checks always run: the output exists, ffprobe can read it, it has a video stream, and its first and last 5 seconds decode without errors.

**Why the size limit defaults to 100 % with rejection on:**
- **Saving space is the point.** An output bigger than its source is a net loss. When originals are deleted after success, it would mean deleting a smaller file to keep a bigger one.
- **It happens more often than you'd expect.** Low-bitrate recordings, files already in H.265 or AV1, and VA-API's
  constant-QP mode can all produce a larger file at a given quality setting.
- **What happens:** the job fails with *The new file was bigger than the original*, the output is discarded, and the
  original is untouched. The failed-attempt guard ([§4.5](#45-the-re-processing-guard)) stops later scans from queueing
  the same file with the same profile. Adjust the profile or the size limit, then press **Retry** on the job.
- **Remuxes** (video *copy*) skip the size check.
- A stricter limit such as 80 % means only real savings count. Turning rejection off is not recommended when originals are deleted.

### 2.6 Scanning, fingerprints and moved files

Each scan walks the folders, fingerprints new or changed files, probes them with ffprobe (codec, resolution, frame
rate, HDR, audio tracks…), and then evaluates the rules.

- **Fingerprint:** an xxHash3-128 over the exact file size plus three 1 MiB samples (start, middle and end). Files
  up to 3 MiB are hashed in full. It stays fast on multi-GB recordings. It identifies files, it doesn't check their
  integrity: it is **not** a full-file or cryptographic (SHA-256) hash.
- **Moves and renames** are matched by fingerprint. A moved file keeps its history and isn't re-encoded, and an output
  FrameForge made is still recognized after a rename.
- **Files still being written** are safe. If a file's size changes between the scan and the start of its job, the job
  stops with *The source changed since it was scanned*, and the next scan picks the file up again.

---

## 3. Profiles & transcoding configuration

### 3.1 Profiles don't name an encoder

A profile describes the **result**: codec, container, quality, resolution and frame-rate caps, audio, and what to
keep. Each node picks the best **verified** encoder for the profile's codec. With Hardware set to **Auto**, the
order is **NVENC → Quick Sync → VA-API → AMF → CPU**. One profile works on both a CPU-only NAS and a GPU desktop.

| Codec | CPU | NVIDIA | Intel | AMD (Linux) |
|---|---|---|---|---|
| H.264 | `libx264` | `h264_nvenc` | `h264_qsv`, `h264_vaapi` | `h264_vaapi` |
| H.265 | `libx265` | `hevc_nvenc` | `hevc_qsv`, `hevc_vaapi` | `hevc_vaapi` |
| AV1 | `libsvtav1` | `av1_nvenc` (RTX 40+) | `av1_qsv`, `av1_vaapi` (Arc, Meteor Lake+) | `av1_vaapi` (RX 7000+) |

### 3.2 The quality slider vs. native encoder values

- **0 = smallest file, 100 = best quality.** Each encoder has its own table of anchor points, with linear
  interpolation between them, rounded to a whole number.
- The mapping aims for *roughly comparable* quality at the same slider value. **The same number does not mean the same
  quality across encoders.** The UI always shows the native value, e.g. *CRF 24*.
- For every native scale, **lower means better quality and a bigger file**.

| Encoder | Scale | 0 | 50 | 70 | 85 | 100 |
|---|---|---|---|---|---|---|
| `libx264` | CRF 0–51 | 34 | 24 | 21 | 19 | 15 |
| `h264_nvenc` | CQ 0–51 | 36 | 27 | 24 | 21 | 16 |
| `h264_qsv` / `h264_vaapi` | ICQ / QP 0–51 | 34 | 26 | 23 | 21 | 17 |
| `libx265` | CRF 0–51 | 36 | 27 | 24 | 21 | 16 |
| `hevc_nvenc` | CQ 0–51 | 38 | 29 | 26 | 23 | 18 |
| `hevc_qsv` | ICQ 0–51 | 36 | 27 | 24 | 22 | 18 |
| `hevc_vaapi` | QP 0–51 | 36 | 28 | 25 | 22 | 18 |
| `libsvtav1` | CRF 1–63 | 52 | 38 | 32 | 27 | 18 |
| `av1_nvenc` | CQ 0–51 | 44 | 34 | 30 | 26 | 20 |
| `av1_qsv` | ICQ 0–51 | 42 | 32 | 28 | 25 | 20 |
| `av1_vaapi` | QP 0–255 | 200 | 140 | 115 | 95 | 70 |

AMF uses the VA-API values.

**Speed** maps to encoder presets:

| Speed | x264 / x265 | SVT-AV1 | NVENC | QSV | VA-API |
|---|---|---|---|---|---|
| Fast | `veryfast` | `10` | `p3` | `veryfast` | no preset |
| Balanced | `medium` | `8` | `p5` | `medium` | no preset |
| Quality | `slow` | `6` | `p6` | `slow` | no preset |
| Max | `slower` | `4` | `p7` | `veryslow` | no preset |

**Advanced overrides** (profile editor → Advanced):
- **Rate control: constant quality** takes a raw CRF/CQ/QP value. It's applied as-is to whichever encoder runs, and
  only clamped to that encoder's range, so a value tuned for x265 lands unchanged on NVENC. Pin **Hardware** when you use it.
- **Rate control: bitrate** targets a bitrate with a 1.5× peak, for predictable file sizes.
- **Encoder preset override** takes a raw preset name, e.g. `p7`.
- **Extra video arguments** are appended to the encoder options. `-i`, `-y`, `-f`, `-map` and `-progress` are rejected.

### 3.3 Built-in profiles and what they resolve to

| Profile | Output | Slider | CPU | NVENC | QSV | VA-API | Audio |
|---|---|---|---|---|---|---|---|
| Maximum Compatibility | H.264 / MP4 | 75 | CRF 20 | CQ 23 | ICQ 22 | QP 22 | AAC 160 kb/s |
| Balanced | H.265 / MP4 | 68 | CRF 24 | CQ 26 | ICQ 24 | QP 25 | kept when it plays widely in MP4, else AAC |
| Space Saver | H.265 / MP4 | 52 | CRF 27 | CQ 29 | ICQ 27 | QP 28 | AAC 128 kb/s |
| Smallest Files (AV1) | AV1 / MP4 | 62 | CRF 34 | CQ 32 | ICQ 30 | QP 125 | Opus 96 kb/s |
| High Quality | H.265 / MKV | 84 | CRF 21 | CQ 23 | ICQ 22 | QP 22 | kept (AAC 256 kb/s if needed) |
| Stream VOD Archive | H.265 / MKV, ≤1080p, ≤60 fps | 60 | CRF 26 | CQ 28 | ICQ 26 | QP 26 | Opus 128 kb/s |
| YouTube Upload | H.264 / MP4 | 85 | CRF 19 | CQ 21 | ICQ 21 | QP 21 | AAC 384 kb/s |
| Editing Proxy (720p) | H.264 / MP4, ≤720p | 45 | CRF 25 | CQ 28 | ICQ 27 | QP 27 | AAC 128 kb/s |
| OBS Remux to MP4 | video copied / MP4 | – | – | – | – | – | copy compatible |

You can edit or delete the built-ins. Deleted built-ins don't come back after a restart.

### 3.4 CPU and GPU setups

**Leave Hardware on Auto in most profiles** and let each node choose. Change it only when you need to control where work runs:

| Goal | Hardware | Fall back to CPU if no GPU encoder is available | Result |
|---|---|---|---|
| One profile for mixed hardware | Auto | on (default) | GPU when a node has one, otherwise CPU |
| Best quality per MB, regardless of speed | CPU | – | Always x264 / x265 / SVT-AV1 |
| Never run slow CPU AV1 | Auto (or a specific backend) | **off** | Jobs wait for a node with a verified hardware encoder. The queue shows *No working … encoder on this node (CPU fallback disabled)*. |

**Per backend:**
- **CPU (x264/x265/SVT-AV1):** best compression per megabyte, and the slowest. AV1 on CPU is slow; for a large
  backlog use an AV1-capable GPU or accept long runtimes.
- **NVENC:** needs the NVIDIA driver, plus the Container Toolkit on Linux. GeForce cards limit how many encodes run
  at once, and OBS and Plex count against that limit. Run 1–2 jobs per GPU.
- **Quick Sync (QSV):** Intel iGPU or Arc on a **Linux** host with `/dev/dri`. Arc needs its HuC firmware loaded.
- **VA-API:** Intel or AMD on Linux (AMD always uses VA-API). It runs in **constant-QP mode**, so the same number
  usually gives **larger files** than CRF or ICQ. Test on a few files and lower the slider if the size check rejects outputs. There's no speed preset.
- **Hardware decoding** (Advanced → *Use hardware decoding when verified*) is on by default. If a file won't decode
  on the GPU, the job restarts with software decoding automatically.

### 3.5 Preserving metadata, HDR, chapters and audio

Whatever the target container can hold is kept. Anything dropped or converted is **recorded as a note on the job**.

| Item | Behavior | Control |
|---|---|---|
| Color tags (primaries, transfer, matrix, range) | Always carried over | – |
| HDR10 / HLG → H.265 or AV1 | Kept as 10-bit. x265 also carries mastering-display and content-light metadata. | Advanced → *Keep 10-bit / HDR when the source has it* (off forces 8-bit) |
| HDR → H.264 | 8-bit, **no tone mapping**, so it looks washed out. The job gets a note. | Use H.265 or AV1 for HDR footage |
| Dolby Vision | Dynamic metadata is dropped; the HDR10/HLG base layer is kept (with a note) | – |
| Audio tracks | **Every track is kept** (game, mic and Discord tracks from OBS all survive) | *Audio*: Copy compatible (default) / Copy (conversions noted) / Transcode (AAC or Opus) |
| Audio bitrate | Per stereo pair: 5.1 gets 3×, capped at 1024 kb/s | Audio codec & bitrate |
| PCM audio into MP4 | Converted automatically | – |
| Subtitles | MKV keeps all. MP4 converts text subtitles to `mov_text` and drops image subtitles (PGS/DVD). | *Keep subtitles* |
| Chapters | Kept | *Keep chapters* |
| Metadata | Copied from the source | *Keep metadata* |
| Attachments (fonts, cover art) | MKV → MKV only | – |
| Data streams (timecode tracks) | Dropped | – |
| `FRAMEFORGE` tag (`job=…;profile=…`) | **Always written**, even with *Keep metadata* off | – |

For multi-track OBS recordings, use **MKV**: it holds every audio and subtitle format.

### 3.6 Test a profile before automating

1. **Profiles → open a profile.** *What FFmpeg will run* shows the exact command and the native quality value for a sample file.
   (The preview always shows `/dev/dri/renderD128`. Real jobs use the device the node detected.)
2. **Files →** open 2–3 typical files **→** pick the profile **→ Add to queue.** Manual jobs default to High
   priority and ignore quiet hours.
3. **Open the job.** Check the encoder used, the native value, the validation results, the size saved and any notes.
4. **Watch the output.** Scrub through dark scenes and fast motion. Adjust the slider and repeat until you're happy.

---

## 4. Building rules & aging policies

### 4.1 How rules are evaluated

- After every scan (and when you click **Apply now**), each analyzed file goes through the enabled rules from **top to bottom**.
- **The first matching rule decides:** *Convert using* a profile at a priority, or *Leave it alone*.
- A file that no rule matches is left alone.
- A rule applies to **All libraries** or to one library.
- **Order matters.** Put protective *Leave it alone* rules at the top. Use the ↑/↓ buttons to reorder.

### 4.2 Aging policy: keep 30 days, then compress

**Rules → Aging policy →** pick the library:

| Stage | Action | Profile | Priority |
|---|---|---|---|
| From 0 days | Keep | – | – |
| After 30 days | Convert | Balanced (H.265) | Normal |
| After 365 days | Convert | Smallest Files (AV1) | Low |

- **Age is measured from:**
  - *File modified date* (default): days since the file was last modified.
  - *Recording date (from metadata)*: the file's `creation_time`, falling back to the modified date. Use it when files were copied or restored recently and their dates are misleading.
- **Only run these jobs overnight (23:00–07:00):** optional window for all of the policy's jobs.
- **Save policy** generates one rule per stage:
  `Aging 0–30 days → keep original`, `Aging 30–365 days → Balanced`, `Aging 365+ days → Smallest Files (AV1)`.
  Each stage matches *age ≥ its start and < the next stage's start*.
- Saving again regenerates those rules in the same position. Rules you wrote yourself are untouched. **Remove policy** deletes only the generated rules.
- The policy applies on the next scan. **Apply now** evaluates and queues immediately.
- Limits: up to 8 stages, in increasing order with no duplicate ages, and every *Convert* stage needs a profile.

**Stage transitions re-encode on purpose.** A file converted to H.265 at 30 days is converted to AV1 after 365 days.
The AV1 encode starts from the H.265 file, because the in-place policies replaced the original.

### 4.3 Compress only H.264 (codec-aware aging)

Aging stages match on **age only**. They skip files already in the stage's target codec, but a file that is
**already AV1** still matches the H.265 stage and gets re-encoded. That usually fails the size check and is left
alone afterwards, but it wastes an encode.

**Quick fix:** add a rule *Video codec is AV1 → Leave it alone* and move it above the aging rules.

**Precise control:** skip the aging policy and build the stages yourself (**Rules → New rule**):

| # | Name | WHEN | THEN |
|---|---|---|---|
| 1 | Protect edits | **ANY of:** Folder / path contains `/edits/` · Folder / path contains `/exports/` | Leave it alone |
| 2 | Keep recent footage | File age less than 30 | Leave it alone |
| 3 | H.264 → H.265 | **ALL of:** File age at least 30 · File age less than 365 · Video codec is H.264 | Convert using Balanced, Normal |
| 4 | Old footage → AV1 | File age at least 365 | Convert using Smallest Files (AV1), Low |

Rule 4 needs no codec condition: *Skip files that are already in the target codec* leaves AV1 files alone.

To include older codecs as well, use *Codec generation is between 1 and 2* in rule 3 instead of *Video codec is H.264*
(1 = MPEG-1/2/4, WMV; 2 = H.264, VC-1, VP8; 3 = H.265, VP9; 4 = AV1, VVC). Codecs outside that list, such as ProRes,
DNxHD or MJPEG intermediates, count as **generation 0**, so *Codec generation at most 2* would match them too.

**More examples:**
- **Container:** *Extension is one of* `flv`, `ts` → *OBS Remux to MP4*. This is lossless and takes seconds.
- **Size:** **ALL of:** File age greater than 60 · Video codec is H.264 · File size greater than 4 GB → *Space Saver*.
- **Hardware-aware:** **ALL of:** File age greater than 365 · GPU encoder online for AV1 → *Smallest Files (AV1)*.

### 4.4 Rule builder essentials

Conditions form a tree. Groups combine their children with **ALL of** (and), **ANY of** (or) or **NONE of** (not), and groups can be nested.

| Group | Fields |
|---|---|
| Age | File age, Recording age (days) |
| File | File size (GB), Extension, Folder / path, File name (contains, starts/ends with, pattern such as `*/2024/*`, regex) |
| Format | Container, Duration (min), Subtitle track count |
| Video | Video codec, Codec generation, Resolution (**short side**, so vertical 1080×1920 counts as 1080p), Frame rate, Bit depth, Overall bitrate (Mb/s), Dynamic range, Is HDR, Orientation |
| Audio | Audio codec (any track), Audio track count |
| Hardware | GPU encoder online for H.264/H.265/AV1 (verified encoders only), Online nodes |
| Schedule | Time of day, Day of week. These are checked **when rules are evaluated** (after scans), not continuously. Use time windows ([§5.5](#55-scheduling-priorities-and-time-windows)) to control when jobs run. |

- **Unknown values never match a positive condition.** A file whose frame rate couldn't be read doesn't match *frame rate > 30*.
- Text comparisons ignore case. Invalid rules (wrong operator, bad regex, non-numeric number) are rejected on save.
- **Live preview:** the editor shows *Matches N of M files right now* with sample file names, before you save.
- **"Why wasn't this converted?"** Open any file on the **Files** page to see the rule trace: each condition, the file's actual value, and which rule decided.

**Per-rule options (THEN section):**
- **Priority:** Normal by default.
- **Skip files that are already in the target codec (unless the profile also downsizes them):** on by default.
- **Only run these jobs between HH:MM and HH:MM:** a rule time window.

### 4.5 The re-processing guard

When a rule matches, FrameForge still **skips** the file if any of these hold:

| Guard | How it's detected | Shown as |
|---|---|---|
| Already processed with this profile | **Any of:** the database says `processed_profile_id` = the rule's profile · the file's `FRAMEFORGE` tag contains `profile=<id>;` · the file's fingerprint is in the processed-fingerprints table (both the original's and the output's fingerprints are recorded when a job completes) | *Already processed with "Balanced"* |
| Already in the target codec | Same codec, and the profile wouldn't lower resolution or frame rate (per-rule option) | *Already H.265; nothing to gain* |
| A previous attempt with this profile failed | Job history | *A previous … attempt failed; retry it from the job page* |

Before any rule runs, files that are ignored, missing, not yet analyzed, unreadable, or already queued are also left alone.

- The three identity checks cover different cases: the database record for normal operation, the tag that travels
  inside the file, and the fingerprint for moves, renames and copies.
- **Aging stages with different profiles still apply, by design.** H.265 at 30 days and AV1 at 365 days are different
  profiles, so the second stage isn't blocked.
- **The guard works per profile.** Editing a profile doesn't re-encode files it already processed. To apply new
  settings to old files, create a new profile.

### 4.6 Why mtime preservation matters

Before an output is moved into place, it gets the original's modification and access times (`os.utime`), plus its
permissions and ownership. Without this, every converted file would become "0 days old", the aging clock would
restart, and the 365-day stage would never arrive on schedule.

Copy tools that don't preserve timestamps (plain `cp`, some sync clients) reset file age. Use `rsync -a` or
`cp -p`, or switch the policy to *Recording date*.

---

## 5. Nodes, hardware acceleration & scheduling

### 5.1 Verify the built-in node

With `FF_ROLE=all` (the default), the server runs a built-in node named `<container hostname> (built-in)`, or
`FF_NODE_NAME` if you set it.

**Nodes →** open the built-in node and check:
- **Status:** Online.
- **Encoders:** lists what passed detection. Only verified encoders are ever used or shown as available.
- **Machine:** CPU model and thread count, and the GPUs it sees.
- **Jobs at the same time:** the page recommends a value, 2 for a GPU and CPU threads ÷ 12 (1–3) for CPU-only nodes.

With `FF_ROLE=server` there is no built-in node, so add a remote node ([§5.3](#53-add-a-remote-node)).

### 5.2 Run capability detection

Detection runs a real **5-frame test encode** for every encoder, plus a decode test per codec, on the actual device.
An encoder listed by `ffmpeg -encoders` is not enough. `--detect` runs the same tests and prints the result without
connecting to the server:

```bash
# Single-container install (server + built-in node)
docker compose exec frameforge python -m frameforge_node --detect

# A remote node started from docker/compose/node.yml
docker compose -f docker/compose/node.yml exec frameforge-node python -m frameforge_node --detect
```

```
GPUs:
  [0] nvidia  NVIDIA GeForce RTX 4080  cuda:0
VA-API/QSV device used: -
Encoders:
  OK   cpu    hevc  libx265
  OK   nvenc  av1   av1_nvenc
  OK   nvenc  hevc  hevc_nvenc
  FAIL qsv    hevc  hevc_qsv     No /dev/dri render device in the container
Hardware decoders:
  OK   nvenc  av1 / h264 / hevc / vp9
```

| Output | Meaning |
|---|---|
| `OK` | Verified. The scheduler may use this encoder. |
| `FAIL … <reason>` | Not usable. The reason says why. |
| `VA-API/QSV device used:` | The render node this node uses for QSV/VA-API (it prefers Intel when several are visible) |
| `Note:` lines | Extra findings from detection |

**Enable a GPU** with an overlay file:

| Hardware | Command |
|---|---|
| NVIDIA on Linux | `docker compose -f docker-compose.yml -f docker/compose/gpu-nvidia.yml up -d` |
| NVIDIA on Windows (Docker Desktop, WSL2) | `docker compose -f docker-compose.yml -f docker/compose/gpu-windows-nvidia.yml up -d` |
| Intel QSV / VA-API, AMD VA-API (Linux only) | `docker compose -f docker-compose.yml -f docker/compose/gpu-intel-amd.yml up -d` |

- **Pass the overlay on every `up`.** Running `docker compose up -d` without it recreates the container without the GPU.
- **Expected FAILs:** QSV/VA-API/AMF always fail under Docker Desktop, because WSL2 has no `/dev/dri`. `av1_nvenc` fails
  on GPUs older than RTX 40. AMF isn't expected in Linux containers.
- After a driver update or a device change, click **Re-detect hardware** on the node's page.
- **Several GPUs:** run one node container per GPU. When you pin a render node, keep its host name inside the
  container (`/dev/dri/renderD129:/dev/dri/renderD129`). See [gpu.md](gpu.md#several-gpus-in-one-machine).

### 5.3 Add a remote node

1. Set **Settings → General → Server URL for nodes** ([§1.6](#16-general-settings-to-set-now)).
2. **Nodes → Add node:** name, hardware type (CPU / NVIDIA / Intel / AMD), jobs at the same time, optional path
   mappings. Copy the token (**shown once**) and the generated compose or `docker run` snippet.
3. On the node machine, save the snippet as `docker-compose.yml`. It already uses the **same FrameForge version** as
   the server (`ghcr.io/issaci22/frameforge:<version>`), so nothing needs to be built. The server rejects nodes with a
   different protocol version: *Protocol mismatch … Update the node image*.
4. Mount the **same media** read-write, with a `PUID`/`PGID` that can write there, then start the node:
   `docker compose up -d` (or `docker compose -f docker/compose/node.yml up -d` if you use that file).
5. The node shows as **Online** within seconds, with its verified encoders. If it doesn't, run `docker logs frameforge-node`.

Nodes connect **outbound** to the server over a WebSocket, so the node machine needs no open ports.

**Path mappings.** Nodes don't receive files over the network. There is no file-transfer mode, so every node needs
the same storage. If a node mounts it at a different path, add a mapping on the node's page:

| Server path | Node path |
|---|---|
| `/media/vods` | `/mnt/vods` |

- The **longest** matching server prefix wins, and only whole folder names match (`/media/vods` doesn't match `/media/vods-old`).
- Job results and messages use **server** paths. Raw FFmpeg and OS errors under *Technical details* show node paths.
- A node that can't see a file declines the job: *This node can't see /media/vods/… (looked for it at /mnt/vods/… on
  this node). Check the node's volume mounts or path mappings.* The scheduler then tries another node.

### 5.4 Node settings

On the node's page, under **Settings**:

| Setting | Effect |
|---|---|
| Jobs at the same time | Parallel jobs on this node (1–16). More rarely means faster. |
| Keep one slot free for Normal or higher priority jobs | With 2+ slots, Low and Background jobs can't take the last free one |
| Don't start if GPU above / Don't start if CPU above | Utilization limits ([§5.6](#56-utilization-limits)) |
| Only work during certain hours | Node working hours |
| Path mappings | Server path → node path |
| Node enabled | Disabled nodes are refused when they connect |
| **Pause** / **Re-detect hardware** (page header) | Pause finishes running jobs and starts no new ones. Re-detect re-runs hardware detection. |

### 5.5 Scheduling: priorities and time windows

The server's scheduler assigns jobs; nodes never pick up work on their own. Every waiting job shows **why** in the
queue, e.g. *Background work runs 23:00–07:00*.

| Priority | Default for |
|---|---|
| Critical | – |
| High | Jobs you queue by hand |
| Normal | New rules, and the first Convert stage of an aging policy |
| Low | Aging stages you add with **Add stage**, and the wizard's AV1 stage |
| Background | Bulk work that should only use spare capacity |

Priorities are strict tiers: a Normal job never waits behind a Low one. You can change a queued job's priority from the queue.

**Three kinds of time window:**

| Window | Where | Limits | Default |
|---|---|---|---|
| **Background work hours** (quiet hours) | Settings → General | *Background jobs* or *Low + Background jobs*, on every node | Off. 23:00–07:00 when enabled. |
| **Rule window** | Rule editor, or the aging policy's overnight toggle | Jobs created by that rule, at any priority | Off |
| **Node working hours** | Node page | Everything on that node | Off |

- All windows use the container's `TZ`, may wrap midnight (`23:00–07:00`), and treat equal start and end times as "all day".
- **Windows only control when a job starts.** A running job finishes even after its window closes.
- Manually queued jobs ignore quiet hours.
- **New files are only found on scans**, so the library's *Scan every* setting is also part of your schedule.

**Choosing a node:** among online, enabled, unpaused nodes with a free slot, inside their hours, under their limits,
and with a verified encoder for the profile, the scheduler prefers **a GPU node**, then **the most free slots**, then
**the lowest CPU load**.

### 5.6 Utilization limits

*Don't start if GPU above* and *Don't start if CPU above* stop a node from starting work while the machine is busy
with **something else**, such as gaming or editing.

- They're only checked while the node runs **no FrameForge jobs**. Otherwise FrameForge's own load would block its second slot.
- Where GPU utilization can't be read (Intel), the GPU limit has no effect. The metric shows *Not reported by this node*.
- A blocked job says so in the queue, e.g. *node-2: GPU busy (93% > 70%)*.

**Example setups:**

| Scenario | Configuration |
|---|---|
| Gaming PC that doubles as a node | Working hours 01:00–08:00 · Don't start if GPU above 30 % |
| Always-on NAS, backlog only at night | Aging stages at Low · Background work hours *Low + Background jobs*, 23:00–07:00 |
| Keep capacity for urgent manual jobs | Jobs at the same time 2+ · *Keep one slot free for Normal or higher priority jobs* |

---

## 6. Final checklist

- [ ] Admin account created. Password saved in a password manager.
- [ ] `FF_SECURE_COOKIES=true` if served over HTTPS. No raw port 8686 exposed to the internet.
- [ ] **Server URL for nodes** set.
- [ ] Libraries added, no *not writable* warnings, and the first scan finished.
- [ ] Backup folder decided (same disk or another one), with a plan for deleting old backups (by hand, or *Keep it for a while*).
- [ ] Size limit left at 100 % with rejection on.
- [ ] 2–3 manual test jobs completed and outputs checked.
- [ ] Rules in the right order: protective skip rules first. The live preview counts look right.
- [ ] `--detect` shows `OK` for the encoders you expect, and the node pages agree.
- [ ] Windows and utilization limits set on shared machines.
- [ ] **Create jobs automatically when rules match** turned on for each library.
- [ ] `/config` included in your backups.

## Where to go next

| Topic | Page |
|---|---|
| Diagnoses and fixes for every failure message | [troubleshooting.md](troubleshooting.md) |
| GPU host requirements and multi-GPU setups | [gpu.md](gpu.md) |
| Crash recovery and file safety internals | [architecture.md](architecture.md) |
