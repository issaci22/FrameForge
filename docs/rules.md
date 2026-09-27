# Rules

Rules decide **which files get converted, with which profile, and when**. After every scan, each file goes through the
rules in order. The **first enabled rule that matches** decides what happens to it:

- **Transcode** with the rule's profile, at the rule's priority (and inside its time window, if it has one), or
- **Skip:** leave the file alone. Put skip rules first to protect folders or kinds of files.

A file that no rule matches is left alone.

Rules can apply to every library or to just one.

## Aging policy (the easy way)

**Rules → Aging policy** covers the most common setup in a single form:

| Age | Action |
|---|---|
| 0 – 30 days | Keep original |
| 30 – 365 days | Balanced (H.265) |
| 365+ days | Smallest Files (AV1) |

Saving generates one rule per stage (`Aging 30–365 days → Balanced`, …), in order. Each stage matches
*age ≥ its start and < the next stage's start*. Editing the policy regenerates its rules in the same position. Rules
you wrote yourself are untouched.

**Age** is either:
- **File age:** days since the file was last modified. FrameForge keeps this date on its outputs, so a converted
  file doesn't become "new" again.
- **Recording age:** the recording date stored in the file (`creation_time`), falling back to the file age. Use it when your
  files were copied or restored recently and their modification dates are misleading.

Moving from one stage to the next re-encodes on purpose: a file converted to H.265 at 30 days is converted to AV1 once it
passes 365 days. It is re-encoded from the H.265 file. Originals kept next to their output are marked as kept originals and left alone
([libraries.md](libraries.md#output-and-originals)), so the later stage converts the output, not the original again.

Choose an optional time window, such as nights only, for all the policy's jobs ([scheduling.md](scheduling.md)).

## Rule builder

A rule's conditions form a tree. Groups combine their children with **ALL of** (and), **ANY of** (or) or
**NONE of** (not), and groups can be nested.

| Group | Field | Operators |
|---|---|---|
| Age | File age, Recording age (days) | greater than, at least, less than, at most, equals, between |
| File | File size (GB) | number operators |
| | Extension | is, is not, is one of, is not one of |
| | Folder / path, File name | contains, starts with, ends with, matches pattern (`*/2024/*`), matches regex |
| Format | Container | enum operators |
| | Duration (min), Subtitle track count | number operators |
| Video | Video codec | enum operators |
| | Codec generation (1 = MPEG-2/4, 2 = H.264, 3 = H.265/VP9, 4 = AV1) | number operators |
| | Resolution (short side, so vertical 1080×1920 counts as 1080p) | number operators |
| | Frame rate, Bit depth, Overall bitrate (Mb/s) | number operators |
| | Dynamic range (SDR / HDR10 / HLG / Dolby Vision), Orientation | is, is not, is one of, is not one of |
| | Is HDR | is yes, is no |
| Audio | Audio codec (any track), Audio track count | enum and number operators |
| Hardware | GPU encoder online for (H.264 / H.265 / AV1) | matches when an online node has a *verified* GPU encoder for that codec |
| | Online nodes | number operators |
| Schedule | Time of day (between HH:MM and HH:MM, may wrap midnight), Day of week | checked when the rule is evaluated (after scans) |

Text comparisons ignore case. **Unknown values never match a positive condition.** A file whose frame rate couldn't be
read doesn't match *frame rate > 30*, but it does match *frame rate does not equal 30*.

Rules with unknown fields, the wrong operator for a field, non-numeric numbers or invalid regular expressions are
rejected when you save them.

Examples:
- *Big old H.264 recordings:* ALL of: File age > 60, Video codec is H.264, File size > 4 GB → Space Saver.
- *Never touch edits:* ANY of: Path contains `/edits/`, Path contains `/exports/` → Skip.
- *Only when a GPU can do AV1:* ALL of: File age > 365, GPU encoder online for AV1 → Smallest Files (AV1).

## Live preview and "why?"

The rule editor previews **how many files the rule would affect right now**, plus a sample list, before you save.

On the Files page, open any file to see the **rule trace**: every rule that was checked, each condition with the
file's actual value (*File age is greater than 30 days: 12 days, no*) and which rule decided. This answers the
question "why didn't this file get converted?"

## Guards that override rules

Even when a rule matches, a file is **skipped** if:

| Guard | Why |
|---|---|
| It was already converted with this profile (by database record, by the FRAMEFORGE tag in the file, or by fingerprint after a move or rename) | Never re-encode an encode with the same settings |
| It's already in the target codec, and the profile wouldn't lower its resolution or frame rate (on by default; per-rule setting *skip if already in target codec*) | No gain, only quality loss |
| A job for this file with this profile failed before | Failed jobs aren't retried automatically forever. Retry from the job page once the cause is fixed. |
| It already has a queued or running job | One job per file |
| It's marked *ignored*, missing, not analyzed yet, or couldn't be analyzed | Nothing safe to do |

Automatic retries of *transient* failures (GPU busy, out of memory, node lost, a hardware-decoding hiccup) are a
separate mechanism. See [scheduling.md](scheduling.md#retries).

## Manual queueing

Files → select → **Queue** runs any profile on any analyzed file, whatever the rules say. Manual jobs start at High
priority and ignore quiet hours.
