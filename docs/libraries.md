# Libraries

A **library** is a set of folders that FrameForge scans, together with the rule for what happens to
the original once a file is converted.

## Folders

Library paths are paths **inside the container**. If your compose file mounts
`/mnt/nas/recordings:/media/vods`, the library path is `/media/vods`. The folder picker in the UI only browses
what the container can see.

A library can have several folders. Files are matched by extension:

`.mkv .mp4 .mov .m4v .flv .ts .m2ts .mts .avi .webm .wmv .mpg .mpeg .mxf`

**Always skipped:**
- hidden files and folders (names starting with `.`), including FrameForge's own
  `.frameforge-tmp` (in-progress outputs) and `.frameforge-originals` (default backups)
- the library's configured output and backup folders, if they sit inside the library
- anything matching an **exclude pattern**. Patterns are shell-style and are matched against both the path
  relative to the library folder and the bare file name, e.g. `replay-*`, `clips/*`, `*.part.mkv`.

## Scanning

A scan walks the folders, fingerprints new or changed files, and probes them with ffprobe (codec, resolution,
frame rate, HDR, audio tracks and so on). It then runs the rules ([rules.md](rules.md)) to decide what to queue.

- **Automatic scans** run every *scan interval* (default 60 minutes, minimum 5).
- **Scan now** on the Libraries page starts one immediately.
- **Automation off** keeps scanning and evaluating, but queues nothing by itself. Use it to watch what rules
  *would* do, and queue files by hand from the Files page.

Files that are still being written are safe. If a file's size changes between the scan and the job start, the job
stops with *The source changed since it was scanned*, and the next scan picks the file up again.

**Moves and renames** are recognized by fingerprint (the file size plus samples from its start, middle and end).
A file moved to another folder keeps its history. It won't be re-encoded, and an output FrameForge
produced is still recognized as its output after a rename.

## Output and originals

Each library answers two questions. In every combination the original stays untouched until the new file has been
validated, moved into place and re-checked, and a failed or cancelled job never touches it. See
[architecture.md](architecture.md#file-safety).

**Where do converted files go?**

| Choice | New file |
|---|---|
| **Next to the original** (default) | Same folder. It takes the original's name (with the new extension) when the original is moved or deleted, or is saved as `name.ff.mp4` when the original stays in place. |
| **In a separate folder** | Written to an output folder, mirroring the library's sub-folders. |

**What happens to the original?**

| Choice | Original | Space freed |
|---|---|---|
| **Keep it** (default) | Never deleted by FrameForge. Next to the original, you choose whether it **moves to a backup folder** (the new file takes its name) or **stays where it is** (the new file is `name.ff.ext`). | Only when you delete it |
| **Keep it for a while** | Kept for 1, 3, 7, 14, 30 or any number of days (up to 3650) after a verified conversion, then deleted, but only after the new file passes its checks again (see below). | After the period |
| **Delete after success** | Deleted as soon as the new file has been validated, moved into place and re-probed. | Immediately |

The Libraries page spells out the combination you picked in one sentence before you save.

Details:
- The **default backup folder** is `.frameforge-originals` inside the library folder the file came from. You can
  choose another folder, including one on a different disk; cross-disk moves are copied and verified before the
  original is removed. On the same disk, backups free no space until they're deleted.
- FrameForge **never overwrites a file it didn't create**. If `stream.mkv` already exists next to `stream.mp4`, the
  job fails with *A file already exists at the output location*, and it fails **before encoding**, not after.
- If a backup with the same name already exists, the new backup gets a suffix: `stream (1).mkv`.
- If the original can't be moved or deleted (permissions, say), you get a warning and it stays put. With in-place
  replacement it is kept as `stream.original.mkv`.
- Outputs keep the original's **modification date**, so age-based rules keep working after a conversion.
- An original that stays in the library next to its output (kept in place, or kept for a while in a separate-folder
  library) is marked as a **kept original**. Rules leave it alone, so a later aging stage converts the output, not
  the original a second time. You can still queue it by hand.
- Older API clients that send the single `output_policy` value (`backup`, `replace`, `output_dir`, `alongside`)
  keep working; it's translated to the two answers above.

### Keeping originals for a while

When a job completes, FrameForge records where the original went, its size and fingerprint, and the new file's size,
fingerprint and FRAMEFORGE tag. When the period is over, it deletes the original **only if every check passes at
that moment**:

- the new file still exists (if you moved it inside the library, it's found by fingerprint), has the same size and
  content, can be read by ffprobe, has a video stream, carries this job's FRAMEFORGE tag, matches the original's
  duration, and decodes cleanly at the start and end;
- the original still has the same size and content, is a regular file (not a link) inside the folder FrameForge put
  it in or found it in;
- no queued or running job uses either file;
- the library still keeps originals for a period.

If any check fails, the original stays and the library lists it as **held back**, with the reason. Held-back
originals are checked again on every pass (every 15 minutes), because many reasons are temporary, such as an
unmounted share.

- **Keep forever** removes an original from the schedule. **Delete now** deletes it before its date, after the
  same checks.
- Changing the period **never brings a deletion forward** on its own: a longer period pushes pending dates back, a
  shorter one only applies to new conversions. *Apply the current period to these* recomputes pending dates on request.
- Switching the library away from *Keep it for a while* pauses pending deletions; switching back resumes them.
- When a later aging stage replaces the new file (H.265 at 30 days, AV1 at 365), the pending original is linked to
  the newest version, which was itself verified against the older one.
- The **server** performs these deletions, so it needs write access to the backup and library folders
  ([troubleshooting.md](troubleshooting.md)). If it can't see a file, the original is held back, not guessed about.

## Validation settings

Every output is checked before the original is touched. Per library, you can set:

| Setting | Default | Meaning |
|---|---|---|
| Duration tolerance | 2 % | Output duration may differ from the source by at most this much |
| Minimum output size | 1 MB | Smaller outputs are treated as broken |
| Size limit | 100 % | Output size relative to the source. At 100 % the output must not be bigger than the original. Empty disables the check. |
| Reject outputs over the size limit | on | **On:** the job fails with *The new file was bigger than the original*, the output is discarded and the original stays. **Off:** the larger output is still used, with a warning. |
| Require audio | on | If the source has audio, the output must too |

Rejecting larger outputs is the default because saving space is the point. A file that is already compressed
efficiently (a low-bitrate recording, or one that is already H.265/AV1) often can't get smaller at the profile's
quality. Such a file fails once and is then left alone. Scans don't queue it again for the same profile; use
*Retry* on the job page after changing the profile or the library's size limit. Remuxes (video `copy`) skip the size
check. Libraries created before this default existed were switched to it when the server was upgraded.

Checks that always run: the output exists, ffprobe can read it, it has a video stream, and the first and last
5 seconds decode without errors.

## Permissions

FrameForge writes as `PUID`/`PGID` ([installation.md](installation.md#permissions)). The Libraries page warns when
a folder isn't writable. Fix that before enabling automation, or every job will fail with *Permission denied*.
