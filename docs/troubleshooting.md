# Troubleshooting

When a job fails, its page shows a **diagnosis**: what happened, likely causes, and FFmpeg's own error lines behind a
disclosure. The full FFmpeg log is on the same page.

Whatever the failure, **the source file is never modified by a failed or cancelled job.**

## Where to look

| What | Where |
|---|---|
| A job's FFmpeg output | Job page → Log (also `/config/logs/jobs/<id>.log`) |
| Server log | Settings → Logs, or `/config/logs/server.log` |
| A node's log | Node page → Logs (`/config/logs/nodes/node-<id>.log` on the server) |
| A remote node before it connects | `docker logs frameforge-node` |
| What hardware a node can use | `docker compose exec frameforge python -m frameforge_node --detect` |

## Diagnoses

### Transcoding

| Diagnosis | What to do |
|---|---|
| **The source file is damaged or incomplete** | Usually an OBS recording that was cut off by a crash or power loss (MP4 without its index), or a file still being copied. Remux or repair it, or record in MKV, which survives crashes. |
| **The source changed since it was scanned** | The file was still being recorded or copied. The next scan picks it up again. |
| **Couldn't analyze the source** | ffprobe can't read it: damaged, still being written, or not a video. |
| **This file can't be converted with this profile** | For example, the file has no video stream. |
| **Unsupported pixel format** | 4:2:2 / 4:4:4 or 12-bit sources that many GPU encoders can't take. Use a CPU profile (Hardware: CPU) for these files. |
| **Out of memory** | Lower the node's concurrency. Very large sources (8K) with slow presets need a lot of RAM. |
| **Transcoding failed** | No known pattern matched. Read FFmpeg's error lines under *Technical details*. |

### GPU problems

| Diagnosis | What to do |
|---|---|
| **FFmpeg could not initialize the NVIDIA encoder** | The container can't see the GPU. Check that the NVIDIA Container Toolkit is installed, that the container was started with the NVIDIA overlay, and that `nvidia-smi` works on the host. |
| **The NVIDIA driver is too old for this FFmpeg** | Update the host driver. |
| **The NVIDIA encoder is busy** | GeForce session limit, shared with OBS and Plex. Lower the node's concurrency. Retried automatically on another node. |
| **This NVIDIA GPU doesn't support the requested encode** | AV1 needs an RTX 40 card; 10-bit H.265 needs Pascal or newer. Use H.265, or a profile with CPU fallback. |
| **FFmpeg could not initialize Intel Quick Sync** | `/dev/dri` not passed through, the render group isn't accessible, the iGPU is disabled in BIOS, or the chip lacks this codec (AV1 needs Arc or Meteor Lake+). |
| **FFmpeg could not use VA-API hardware acceleration** | Same checks as above. For AMD, confirm the `amdgpu` driver is loaded. |
| **The hardware decoding pipeline failed** | This file doesn't decode on the GPU. The node already retried with software decoding. If you see this, both attempts failed. |
| **The encoder isn't available in this FFmpeg build** | The node's capability info is stale. Click *Re-detect* on the node. |

A GPU node shows no hardware encoders? Run `--detect`. Every `FAIL` line says why. Common causes:
- *No /dev/dri render device in the container:* the GPU overlay wasn't used, or you're on Docker Desktop, which can't pass Intel/AMD through.
- *No NVIDIA GPU visible:* the NVIDIA runtime isn't configured.
- *A GPU is visible but no hardware encoder passed its test:* a permission or driver problem. Check `ls -l /dev/dri` on the host.

See [gpu.md](gpu.md).

### Files and storage

| Diagnosis | What to do |
|---|---|
| **Permission denied** | `PUID`/`PGID` don't own the media, the volume is mounted `:ro`, or SMB/NFS permissions block writes. The Libraries page warns about unwritable folders. |
| **File not found** / *This node can't see …* | The node mounts the media at another path. Add a path mapping on the node's page ([nodes.md](nodes.md#shared-storage-and-path-mappings)). The node declines the job, and it goes to another node. |
| **Not enough free space** / **The disk is full** | A job needs room for a new copy of the file next to where it will end up. Free up space, or point the library's output to another disk. |

### Validation (checking the output)

| Diagnosis | What to do |
|---|---|
| **The new file was bigger than the original** | The output was discarded and the original left untouched, because keeping it would have used more space. The source is probably already efficiently compressed, or the profile's quality is higher than it needs. Lower the quality (or use a more efficient codec) and click *Retry*. To accept larger outputs, raise the size limit or turn off rejection in the library's validation settings ([libraries.md](libraries.md#validation-settings)). |
| **The output didn't pass validation** | Another check failed (the explanation names it): e.g. the duration is off, audio is missing, or the end doesn't decode. The output was discarded and the original left untouched. A cut-short encode usually means a damaged section in the source. |

### Finishing (replacing the original)

| Diagnosis | Meaning |
|---|---|
| **A file already exists at the output location** | A file FrameForge didn't create already has the output name (e.g. `stream.mkv` next to `stream.mp4`, or an earlier conversion by another aging stage). Nothing was overwritten, and the job stopped before encoding. Rename or move that file, then retry. |
| **Output would overwrite the original** | The library keeps originals in place, but the output would have the same name. Choose *Move it to a backup folder* or a separate output folder. |
| **No backup folder configured** | Keeping originals in a backup folder needs one. |
| **The output failed its final check** | The file couldn't be read back correctly after being moved into place (a storage or network-share error). Everything was rolled back and the original restored. |
| **Finishing was interrupted; original restored** | The node stopped mid-replacement and the new file was lost. The original was put back exactly as it was, and the job runs again. |
| **Finishing never started** / **never completed** | The node stopped before or during the move. The original is untouched, and the job runs again. |
| **Couldn't recover this job automatically** | While the node was offline, someone moved or deleted both the original and its parked copy. Check the library folder and `.frameforge-tmp` by hand. |

Warnings (the job still succeeds):
- *Couldn't back up / delete the original … it is still at …* (or *… kept as `name.original.ext`*): the new file is in place,
  but the original couldn't be moved. It's safe, only not tidied up.
- *Smaller than source: Output is 140.0% of the original*: the output is bigger than the source, and the library allows
  that (**Reject outputs over the size limit** is off). The larger file replaced the original. Turn the setting back on
  to keep originals in this case.

### Originals kept for a while

The library card lists originals waiting for deletion. One that is **held back** shows why; it's checked again every
15 minutes and deleted once every check passes.

| Reason | What to do |
|---|---|
| *The converted file is missing* / *changed since it was verified* | The new file was moved out of the library, edited or replaced. Nothing is deleted while the original is the only good copy. Restore the file, or choose **Keep forever**. |
| *isn't the one FrameForge produced (its FRAMEFORGE tag doesn't match)* | Another file sits at the output path. Check it, then keep or delete the original yourself. |
| *The original changed after it was converted* | Someone re-saved or replaced the original. It's no longer the file that was converted, so it's never deleted automatically. |
| *outside … FrameForge only deletes where it put or found it* / *not a regular file* | The original was moved elsewhere or replaced with a link. |
| *Job N is using this file* | Waits until that job finishes. |
| *Couldn't delete the original: Permission denied* | The **server** deletes timed originals, so the server container needs write access to the library and backup folders, not only the nodes. |
| *The original didn't end up where planned* | The backup move failed during finishing (see the job's warnings). Check the file and delete it yourself. |

**Paused** means the library no longer keeps originals for a period, or was removed: nothing is deleted until you
switch it back.

### Compression previews

- *needs a newer FrameForge node for previews*: update the node image; older nodes can't make previews.
- *all slots are busy with jobs*: a preview needs a free job slot on its node. Try again when a job finishes, or raise
  the node's concurrency.
- *stopped responding* / *took too long*: the node went quiet for a minute, or a preview ran over 10 minutes (CPU AV1
  on 4K can be slow). Try a hardware encoder or a smaller file.

## Common questions

**Jobs stay "queued" forever.** Read the waiting reason in the queue. The usual causes:
- quiet hours or a rule window (*Background work runs 23:00–07:00*)
- all nodes paused or busy
- no node with an encoder for the profile's codec (e.g. AV1 with CPU fallback off)

**Nothing gets queued after a scan.** Is automation on for the library? Open a file on the Files page to see the rule
trace, which explains exactly which condition didn't match.

**A converted file was converted again.** It shouldn't be with the same profile. Converting again with a *different*
profile is intended for aging policies (H.265 → AV1): the later stage converts the newest output, and originals kept
next to their output are left alone. If it happened with the same profile, please report it with the file's rule trace.

**The UI says "Reconnecting".** The browser lost the live-update WebSocket. If you use a reverse proxy, allow WebSocket
upgrades on `/api/v1/events`.

**A remote node won't connect.**
- *Invalid node token*: regenerate the token and update the node.
- *Protocol mismatch*: update the node image to the server's version.
- Connection refused or a timeout: `FF_SERVER_URL` must be reachable **from the node**. `localhost` means the node itself.
  Set **Settings → Server URL for nodes** to the server's LAN address and regenerate the node's token.

**Files show the wrong age.** File age uses the modification date. If you copied footage to the NAS recently, use
*Recording age* in the aging policy ([rules.md](rules.md#aging-policy-the-easy-way)).
