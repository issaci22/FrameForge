# Profiles

A **profile** describes what an output should look like: container, codec, quality, size limits, audio, and what
to keep. It doesn't name an encoder. Each node picks the best *verified* encoder for the profile's codec
([gpu.md](gpu.md)), so one profile works on a CPU-only NAS and on a GPU box.

Where the new file goes and what happens to the original are **library** settings, not profile settings
([libraries.md](libraries.md#output-and-originals)).

## The editor

The editor is organized the way you'd describe an output, with the technical settings tucked away:

1. **Goal**: a new profile starts from a built-in goal. Change anything afterwards; the editor shows
   *Based on Balanced · customized* and can reset to the starting point.
2. **Quality**: the *Smaller file ←→ Better quality* slider, encoding effort, and an optional compression preview.
3. **Format**: container and video codec as separate choices, with what each implies and whether an online node can
   encode it (**GPU**, **CPU** only, or **none**).
4. **Audio**: keep the original tracks when possible, or convert every track.
5. **Advanced** (collapsed): hardware, rate control, raw presets and arguments, streams and metadata, and the exact
   FFmpeg command for each backend on a sample file.

Advice appears next to the setting it's about, with a one-click fix when there is one (for example *Opus in MP4
doesn't play in Premiere → Use AAC*). Only combinations FFmpeg can't produce block saving.

## Built-in profiles

The built-ins are named for a goal:

| Profile | Output | Use it for |
|---|---|---|
| **Maximum Compatibility** | H.264 + AAC, MP4, quality 75 | Files that must play on anything: old TVs, every browser and editor |
| **Balanced** | H.265, MP4, quality 68, audio kept when it plays widely in MP4 (else AAC) | The default archive: about half the size of typical H.264 recordings, hard to tell apart |
| **Space Saver** | H.265, MP4, quality 52, slower preset, AAC 128 kb/s | Footage you want to keep but will rarely watch |
| **Smallest Files (AV1)** | AV1 + Opus, MP4, quality 62 | The smallest files. Fast on RTX 40, Intel Arc and RX 7000; slow on the CPU. |
| **High Quality** | H.265, MKV, quality 84, slower preset, everything kept as-is | Footage you may re-edit; keeps every audio, subtitle and attachment |
| **YouTube Upload** | H.264, MP4, quality 85, AAC 384 kb/s | Upload-ready copies (pair with a separate-folder library) |
| **Stream VOD Archive** | H.265, ≤1080p, ≤60 fps, quality 60, Opus 128 kb/s | Long livestream recordings |
| **OBS Remux to MP4** | Video copied, MP4 | Making OBS MKVs editor-friendly, losslessly, in seconds |
| **Editing Proxy (720p)** | H.264, ≤720p, quality 45, MP4 | Smooth editing proxies (use a separate-folder library) |

Why MP4 for most goals: editors (Resolve, Premiere), Plex direct play, phones and browsers handle it best. MP4 can't
hold image subtitles (PGS, DVD) or attachments; those are dropped **and listed on the job**. High Quality uses MKV so
nothing is ever dropped.

You can edit or delete the built-ins. Deleted built-ins don't come back after a restart.

**Upgrading from the first lineup.** *YouTube Archive*, *Storage Saver* and *Long-Term Archive (AV1)* become
*Balanced*, *Space Saver* and *Smallest Files (AV1)*, once, and only if you never changed them (same name, same
settings). They keep their id, so rules using them keep working and simply use the new settings for new jobs. A
built-in you had changed is left exactly as it is (shown under *From an earlier version*) and the new profile is
added next to it. The Profiles page shows a one-time notice listing what was renamed.

## The quality slider

Quality is a 0–100 slider: 0 is the smallest file and 100 the best quality. Every encoder family has its own
rate-control scale, so **the slider is mapped separately for each encoder**. The mapping aims for roughly
comparable visual quality at the same slider value. That makes it a starting point, not a guarantee: the same
number does *not* mean identical quality across codecs. The UI always shows the native value, e.g. *CRF 24*.

What the editor shows as you move the slider:
- a plain-language band: **Smallest**, **Compact**, **Balanced**, **High** or **Near source**, with what it means for
  typical footage;
- a relative file-size meter. It is deliberately qualitative: the real size depends on the footage far more than on
  the number;
- the **native value on your own nodes** (e.g. *CQ 26 · Desktop*, *CRF 24 · NAS*), with every encoder's value
  behind a disclosure;
- once a profile has at least 3 completed jobs since its last change, **what it really did**: *outputs averaged 41%
  of the original size*.

Native values. Lower means better quality and a bigger file. The values in between are interpolated:

| Encoder | Scale | 0 | 30 | 50 | 70 | 85 | 100 |
|---|---|---|---|---|---|---|---|
| `libx264` | CRF (0–51) | 34 | 28 | 24 | 21 | 19 | 15 |
| `h264_nvenc` | CQ (0–51) | 36 | 31 | 27 | 24 | 21 | 16 |
| `h264_qsv` | ICQ (0–51) | 34 | 29 | 26 | 23 | 21 | 17 |
| `h264_vaapi` | QP (0–51) | 34 | 29 | 26 | 23 | 21 | 17 |
| `libx265` | CRF (0–51) | 36 | 31 | 27 | 24 | 21 | 16 |
| `hevc_nvenc` | CQ (0–51) | 38 | 33 | 29 | 26 | 23 | 18 |
| `hevc_qsv` | ICQ (0–51) | 36 | 31 | 27 | 24 | 22 | 18 |
| `hevc_vaapi` | QP (0–51) | 36 | 31 | 28 | 25 | 22 | 18 |
| `libsvtav1` | CRF (1–63) | 52 | 44 | 38 | 32 | 27 | 18 |
| `av1_nvenc` | CQ (0–51) | 44 | 38 | 34 | 30 | 26 | 20 |
| `av1_qsv` | ICQ (0–51) | 42 | 36 | 32 | 28 | 25 | 20 |
| `av1_vaapi` | QP (0–255) | 200 | 164 | 140 | 115 | 95 | 70 |

AMF uses the same values as VA-API.

VA-API runs in constant-QP mode, so the same number usually gives larger files than CRF or ICQ. Test your
profile on a couple of files before letting an aging policy loose on a whole library.

**Speed** (Fast / Balanced / Quality / Max) maps to encoder presets: x264/x265 `veryfast`…`slower`,
SVT-AV1 `10`…`4`, NVENC `p3`…`p7`, and QSV `veryfast`…`veryslow`. VA-API has no portable preset, so the
setting has no effect there.

## Compression preview

**Preview on a file…** encodes three short samples of a file you pick (at 20%, 50% and 80% of its length) exactly
like a real job would, on a real node with the encoder that node would use, and compares frames with the original:

- a draggable **split** view, or an **A / B** toggle (hold to see the original);
- **zoom** to 2× or 4× and drag to look around, with real pixels (no smoothing), to spot blocking in dark areas and
  gradients, smeared textures, banding and halos around edges;
- a **rough video size** range from the samples, labelled as such (video only).

Each sample starts 2 seconds before the inspected moment so the encoder has settled; the first frame of any encode
looks better than typical frames. When the profile lowers the resolution, the encoded frame is scaled back up so you
compare like with like. HDR frames are shown without tone mapping.

Previews are optional and never touch the library: samples live in the node's own folder, the images travel to the
server, and they're kept for a few hours. A preview takes one job slot on its node while it runs (so it can't push a
GPU past its session limit), runs one at a time per node, and stops when you close the dialog. Nodes from older
FrameForge versions can't make previews; the button says why when no node can.

## Resolution and frame rate limits

- **Max resolution** limits the **short side**, so a 1080 cap turns 4K landscape into 1920×1080 and a
  vertical 2160×3840 short into 1080×1920. Rotated phone footage is handled in its displayed orientation.
- **Max frame rate** only lowers the frame rate (60 → 30). It never adds frames.
- FrameForge **never upscales** resolution or frame rate.

## HDR and color

- Color tags (primaries, transfer, matrix, range) are always carried over.
- **H.265 and AV1** keep HDR10 and HLG as 10-bit, including mastering-display and content-light metadata on x265.
- **H.264** outputs are 8-bit. HDR sources come out without tone mapping and will look washed out. The job gets a note saying so.
- Dolby Vision dynamic metadata isn't carried over; the HDR10/HLG base layer is kept, and the job gets a note.
- **10-bit: never** forces 8-bit output even for 10-bit sources.

## Audio

| Choice | Behavior |
|---|---|
| **Keep original when possible** (default) | Copy each track the container can hold; convert the others to the profile's codec. Conversions are listed on the job. |
| **Convert every track** | Re-encode every track to the chosen codec |

Codecs to convert to: **AAC** (plays everywhere), **Opus** (best quality per bit; not in Premiere, Final Cut or older
Apple devices), **AC-3** and **E-AC-3** (Dolby Digital for receivers and TVs; up to 5.1, so 7.1 is downmixed with a
note), and **FLAC** (lossless, no bitrate).

With MP4 you can also convert tracks that are *storable* but play poorly there (Opus, FLAC) instead of copying them.
Balanced does this by default. Profiles using AC-3, E-AC-3, FLAC or this option only run on nodes from this version
on; older nodes are skipped with a clear reason.

Every audio track is kept (game audio, mic and Discord tracks from OBS all survive). The bitrate applies per stereo pair,
so 5.1 gets three times the stereo bitrate, capped at 1024 kb/s. PCM can't be stored in MP4 and is converted automatically.

## Subtitles, chapters, metadata, attachments

Everything is kept when the target container can hold it. Whatever gets dropped is **written on the job as a note**:
- MKV keeps all subtitles.
- MP4 converts text subtitles (SRT, ASS, WebVTT) to `mov_text` and drops image subtitles (PGS, DVD).
- Attachments (fonts, cover art) are kept for MKV → MKV only.
- Data streams (timecode tracks) are dropped.

Every output gets a `FRAMEFORGE` metadata tag (`job=…;profile=…`) so FrameForge recognizes its own work later.

## Advanced

| Setting | Effect |
|---|---|
| Rate control: constant quality | Enter the native value (CRF/CQ/QP) directly instead of using the slider |
| Rate control: bitrate | Target bitrate with a 1.5× peak, for when you need a predictable size |
| Encoder preset | Raw preset override for the chosen encoder |
| Extra video arguments | Appended to the video encoder options. Input, output, `-map` and `-progress` are managed by FrameForge and rejected here. |
| Hardware: Auto / CPU / NVENC / QSV / VA-API / AMF | Force a backend. With *CPU fallback* off, a job waits instead of falling back to the CPU. |
| Hardware decoding | On by default. Automatically falls back to software decoding when a file won't decode on the GPU. |
| MP4 fast start | Moves the index to the front so the file plays in browsers before it has fully downloaded |
