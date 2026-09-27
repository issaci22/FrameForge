# GPU acceleration

FrameForge never assumes a GPU works just because FFmpeg lists an encoder. At startup, and whenever you click
**Re-detect** on a node, each node runs a short real test encode (5 frames) for every encoder it has. For hardware
decoding it also runs a real decode test per codec. Only encoders that pass are used, and only they show as available in the UI.

With the hardware setting on **Auto**, a profile uses the first verified backend in this order:
**NVENC → Quick Sync → VA-API → AMF → CPU**. If hardware decoding fails on a particular file, the job retries
automatically with software decoding.

## Check what works

This runs the same tests the node runs and prints the results. No server connection is needed:

```bash
docker compose exec frameforge python -m frameforge_node --detect
```

```
GPUs:
  [0] nvidia  NVIDIA GeForce RTX 4080  cuda:0
Encoders:
  OK   nvenc  av1   av1_nvenc
  OK   nvenc  hevc  hevc_nvenc
  FAIL qsv    hevc  hevc_qsv     No /dev/dri render device in the container
  ...
```

Every `FAIL` line says why. The node's page in the web UI shows the same information.

## NVIDIA (NVENC / NVDEC)

**Host:** the NVIDIA driver (`nvidia-smi` works) plus:
- **Linux:** the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  followed by `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
- **Windows:** Docker Desktop with the WSL2 backend and a current NVIDIA Windows driver.

```bash
docker compose -f docker-compose.yml -f docker/compose/gpu-nvidia.yml up -d
```

| GPU generation | H.264 | H.265 | H.265 10-bit | AV1 encode |
|---|---|---|---|---|
| GTX 10 (Pascal) and newer | yes | yes | yes | no |
| RTX 40 (Ada) and newer | yes | yes | yes | yes |

GeForce cards limit how many encodes can run at once, and OBS and Plex count against that limit. If jobs fail with
*The NVIDIA encoder is busy*, lower the node's concurrency.

FFmpeg encodes on the first NVIDIA GPU the container can see. To use several NVIDIA cards, run one node per GPU,
each with its own `device_ids`.

### Windows: Docker Desktop (WSL2), with an Intel iGPU too

A common creator PC is an NVIDIA card plus an Intel CPU with integrated graphics, for example an RTX 4080 with a
Core i7-13700K (UHD Graphics 770). Use the Windows overlay:

```bash
docker compose -f docker-compose.yml -f docker/compose/gpu-windows-nvidia.yml up -d
docker compose exec frameforge python -m frameforge_node --detect
```

**How NVENC gets into the container.** On Linux, the NVIDIA Container Toolkit injects the driver libraries into
containers. On Windows you don't install it: the Windows NVIDIA driver exposes the GPU to WSL2 through `/dev/dxg`
and ships the CUDA/NVENC libraries for WSL, and Docker Desktop's built-in GPU support mounts them into any container
that reserves an NVIDIA device. You need:
- a current NVIDIA driver on **Windows** (don't install a Linux NVIDIA driver inside WSL, it breaks this),
- Docker Desktop with the WSL2 backend.

**Why Quick Sync doesn't work here.** WSL2 has no `/dev/dri`. GPUs appear only as `/dev/dxg`, a DirectX
paravirtualization device that Intel's QSV and VA-API drivers can't use. Docker Desktop's VM has no `/dev/dri` even
for privileged containers, and adding `devices: /dev/dri` makes container creation fail. So the UHD 770 (or any
Intel or AMD GPU) can't encode for FrameForge under Docker Desktop. The overlay leaves it out on purpose. Keeping the
iGPU enabled in the BIOS is harmless and still helps Windows apps such as OBS. For Quick Sync, run a node on a Linux host.

Verified on an RTX 4080 + i7-13700K under Docker Desktop (WSL2 kernel 6.18):

```
GPUs:
  [0] nvidia  NVIDIA GeForce RTX 4080  cuda:0
VA-API/QSV device used: -
Encoders:
  OK   cpu    av1   libsvtav1
  OK   cpu    h264  libx264
  OK   cpu    hevc  libx265
  OK   nvenc  av1   av1_nvenc
  OK   nvenc  h264  h264_nvenc
  OK   nvenc  hevc  hevc_nvenc
  FAIL qsv    hevc  hevc_qsv     No /dev/dri render device in the container
  FAIL vaapi  hevc  hevc_vaapi   No /dev/dri render device in the container
  ...
Hardware decoders:
  OK   nvenc  av1 / h264 / hevc / vp9
```

The QSV, VA-API and AMF `FAIL` lines are expected on this setup. With the hardware setting on **Auto**, every
profile uses NVENC.

## Intel (Quick Sync / VA-API) and AMD (VA-API)

**Linux host only.** Docker Desktop on Windows/macOS cannot pass `/dev/dri` through
(see [Windows: Docker Desktop](#windows-docker-desktop-wsl2-with-an-intel-igpu-too)).

```bash
ls -l /dev/dri          # needs renderD128 (and more if you have several GPUs)
docker compose -f docker-compose.yml -f docker/compose/gpu-intel-amd.yml up -d
```

You don't need `group_add`: the entrypoint joins whatever group owns each render node on your host.

| Hardware | Kernel | Backends | AV1 encode |
|---|---|---|---|
| Intel iGPU, 6th–10th gen | any recent | QSV, VA-API | no |
| Intel iGPU, 11th gen and newer / Meteor Lake | any recent | QSV, VA-API | Meteor Lake+ only |
| Intel Arc A-series (Alchemist) | 6.2+ (6.8+ recommended) | QSV, VA-API | yes |
| Intel Arc B-series (Battlemage) | 6.12+ (`xe` driver) | QSV, VA-API | yes |
| AMD Radeon / Ryzen APU (VCN) | any recent (`amdgpu`) | VA-API | RX 7000 (RDNA3) and newer only |

The image includes jellyfin-ffmpeg with its own Intel media driver and Mesa VA-API drivers. The host only needs
the kernel driver. On Linux, AMD GPUs are used through VA-API. AMF is detected if present, but it isn't expected
in a Linux container.

### Several GPUs in one machine

A node uses **one** VA-API/Quick Sync device. When it sees several, it prefers an Intel one. The `--detect`
output lists every GPU it sees and which device it picked (`VA-API/QSV device used: …`).

#### Example: Intel Arc + AMD Ryzen 5 PRO 4650G

The 4650G's Radeon (Vega) graphics and an Arc card are two separate VA-API devices:

| GPU | Backends | Encode | AV1 encode | Hardware decode |
|---|---|---|---|---|
| Intel Arc A-series | QSV, VA-API | H.264, H.265 (incl. 10-bit), AV1 | yes | H.264, H.265, VP9, AV1 |
| Vega (Ryzen 4000G APU) | VA-API | H.264, H.265 | no | H.264, H.265, VP9 (no AV1) |

This table is what the hardware supports. What your host actually runs is whatever `--detect` reports as `OK`.

**1. Make both GPUs visible on the host.**
- Many AM4 boards switch the iGPU off when a graphics card is installed. If you want to use the Vega, enable it in
  the BIOS (the setting is called something like *Integrated Graphics: Force*, *iGPU Multi-Monitor*, or sits under
  *AMD CBS → NBIO → GFX Configuration*).
- The Arc needs kernel 6.2+ (6.8+ recommended) and linux-firmware new enough for DG2. `sudo dmesg | grep -i huc`
  should show HuC as loaded/authenticated. Quick Sync encoding on Arc depends on it.
- Check which render node is which. `by-path` links each PCI address to its `renderD` node:

  ```bash
  ls -l /dev/dri/by-path/            # pci-0000:03:00.0-render -> ../renderD128
  lspci | grep -Ei 'vga|display'     # 03:00.0 VGA ... Intel ... DG2 [Arc A380]
  ```

  The numbers depend on probe order. They can change when you add or remove a card or change BIOS graphics
  settings, so check again after hardware changes.

**2. Choose a setup.**

*Simple: Arc only.* Use the overlay as it is, which passes all of `/dev/dri`. The node picks the Arc because it prefers Intel.
It encodes H.264, H.265 and AV1 with Quick Sync and VA-API. The Vega is visible but unused.

*Both GPUs at once.* Each GPU becomes its own node, with its own concurrency, visible side by side on the Nodes page.
1. Give the main container only the Arc's render node. In `docker/compose/gpu-intel-amd.yml`, replace
   `/dev/dri:/dev/dri` with that node:

   ```yaml
       devices:
         - /dev/dri/renderD128:/dev/dri/renderD128   # the Arc on this host
   ```

2. Add a node (**Nodes → Add node**, hardware *AMD*), then run a second container on the same machine from
   [docker/compose/node.yml](../docker/compose/node.yml) with only the Vega's render node:

   ```yaml
       devices:
         - /dev/dri/renderD129:/dev/dri/renderD129   # the Vega on this host
   ```

   In that file, `FF_SERVER_URL` must be the host's LAN address (e.g. `http://192.168.1.20:8686`). `localhost` inside the node
   container is the node itself. Mount the media at the same paths as the main container, so no path mapping is needed.
3. With hardware set to **Auto**, each node uses its own best verified encoder, and the scheduler prefers a node that
   can encode in hardware over one that would use its CPU. H.264/H.265 jobs go to whichever GPU node has a free slot.
   The Vega has no AV1 encoder, so while the Arc is busy an AV1 job can still land on the Vega node and encode on
   the 4650G's six CPU cores, which is slow. To keep AV1 on the Arc, turn off **Fall back to CPU if no GPU encoder is
   available** in the AV1 profile. AV1 jobs then wait for the Arc.

**Keep the same device name inside the container** (`renderD129:renderD129`, never `renderD129:renderD128`).
The node reads each device's vendor from the host's `/sys/class/drm/<name>`, so a renamed device reports the other
GPU's vendor, and Quick Sync gets tried on the wrong chip.

You don't need `/dev/dri/card*` for transcoding. Render nodes are enough.

### Verifying Intel/AMD on your host

QSV/VA-API support has only been tested in code so far, not on real Intel/AMD hardware. These steps check it on yours:

1. Run `--detect` (above) in each container.
   - A working Arc lists `VA-API/QSV device used:` with the Arc's render node and shows `OK` for `h264_qsv`, `hevc_qsv`,
     `av1_qsv` and the `*_vaapi` encoders, plus the VA-API decoders.
   - A working Vega node shows `OK` for `h264_vaapi` and `hevc_vaapi`. `av1_vaapi` fails, which is expected, and QSV
     lines say *Quick Sync needs an Intel GPU*.
2. If a GPU is missing or every test fails, ask the driver directly from inside the container:

   ```bash
   docker compose exec frameforge vainfo --display drm --device /dev/dri/renderD128
   ```

   It should name the driver (*Intel iHD driver* for Arc, *Mesa Gallium driver … radeonsi* for AMD) and list
   `VAEntrypointEncSlice` lines for the codecs it can encode.
3. Queue one file with the **Smallest Files (AV1)** profile (Files → select → Queue). The job page shows the encoder
   it used (e.g. `av1_qsv`) and whether hardware decoding was on. Queue an H.265 file too, to exercise the Vega node.
4. If something fails, the job page explains why. See also [troubleshooting.md](troubleshooting.md#gpu-problems).

## Metrics

GPU utilization, VRAM and temperature are shown when the node can read them: `nvidia-smi` for NVIDIA,
`amdgpu` sysfs for AMD. Intel GPU utilization needs extra tooling that isn't in the image, so it is shown as
*not reported* rather than guessed.
