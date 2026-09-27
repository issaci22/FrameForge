"""Detect what this machine can actually do. Every hardware path is VERIFIED with a real test."""

from __future__ import annotations

import glob
import logging
import os
import platform
import re
import socket
import time
from pathlib import Path

import psutil

from frameforge_shared.codecs import AUDIO_ENCODERS, ENCODER_INFO, Backend
from frameforge_shared.protocol import AudioEncoderCapability, DecoderCapability, EncoderCapability, GpuInfo, NodeCapabilities, StorageInfo
from frameforge_shared.tools import ffmpeg_path, find_tool

from .proc import run

log = logging.getLogger(__name__)

_TEST_SOURCE = ["-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=0.3"]
_AUDIO_TEST_SOURCE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.1"]
_SAMPLE_ENCODERS = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9"}
_VENDOR_IDS = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
_HW_FAIL_MARKERS = ("Failed setup for format", "hwaccel initialisation returned error", "Device creation failed", "No device available", "Failed to", "Could not dynamically load")


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return f"{line.split('=', 1)[1].strip(chr(34))} (kernel {platform.release()})"
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


async def _ffmpeg_encoders() -> set[str]:
    res = await run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=20)
    names = set()
    for line in res.stdout.splitlines():
        m = re.match(r"^\s*[VAS][\w.]{5}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


async def _nvidia_gpus() -> list[GpuInfo]:
    smi = find_tool("nvidia-smi")
    if not smi:
        return []
    res = await run([smi, "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits"], timeout=15)
    gpus = []
    if res.returncode != 0:
        return gpus
    for line in res.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                gpus.append(GpuInfo(index=int(parts[0]), vendor="nvidia", name=parts[1], vram_total_mb=int(float(parts[2])), driver=parts[3], device=f"cuda:{parts[0]}"))
            except ValueError:
                continue
    return gpus


async def _dri_gpus(start_index: int) -> list[GpuInfo]:
    gpus = []
    vainfo = find_tool("vainfo")
    for i, dev in enumerate(sorted(glob.glob("/dev/dri/renderD*"))):
        node = os.path.basename(dev)
        vendor_id = ""
        try:
            vendor_id = Path(f"/sys/class/drm/{node}/device/vendor").read_text().strip()
        except OSError:
            pass
        vendor = _VENDOR_IDS.get(vendor_id, "unknown")
        if vendor == "nvidia":
            continue  # handled via nvidia-smi / CUDA
        name = f"{vendor.upper()} GPU" if vendor != "unknown" else "GPU"
        driver = None
        try:
            product = Path(f"/sys/class/drm/{node}/device/product_name").read_text().strip()
            if product:
                name = product
        except OSError:
            pass
        if vainfo:
            res = await run([vainfo, "--display", "drm", "--device", dev], timeout=15)
            m = re.search(r"Driver version:\s*(.+)", res.stdout + res.stderr)
            if m:
                driver = m.group(1).strip()
                if name.endswith("GPU"):
                    name = driver.split(" - ")[0]
        vram = None
        try:
            vram = int(Path(f"/sys/class/drm/{node}/device/mem_info_vram_total").read_text().strip()) // (1024 * 1024)
        except (OSError, ValueError):
            pass
        gpus.append(GpuInfo(index=start_index + i, vendor=vendor, name=name, vram_total_mb=vram, driver=driver, device=dev))
    return gpus


def _hw_args(backend: str, device: str | None) -> tuple[list[str], list[str]]:
    """(input-side args, filter args) to feed a lavfi software frame into a HW encoder."""
    if backend == Backend.VAAPI and device:
        return ["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"], ["-vf", "format=nv12,hwupload"]
    if backend == Backend.QSV and device:
        return (
            ["-init_hw_device", f"vaapi=va:{device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"],
            ["-vf", "format=nv12,hwupload=extra_hw_frames=16,format=qsv"],
        )
    return [], ["-pix_fmt", "yuv420p"] if backend == Backend.CPU else ["-pix_fmt", "nv12"]


async def _test_encoder(name: str, backend: str, device: str | None) -> tuple[bool, str | None]:
    pre, filt = _hw_args(backend, device)
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", *pre, *_TEST_SOURCE, *filt, "-frames:v", "5", "-c:v", name, "-f", "null", "-"]
    res = await run(args, timeout=30)
    if res.returncode == 0:
        return True, None
    lines = [ln for ln in res.stderr.strip().splitlines() if ln.strip()]
    return False, (lines[-1] if lines else f"exit code {res.returncode}")[:300]


async def _test_audio_encoder(name: str) -> tuple[bool, str | None]:
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", *_AUDIO_TEST_SOURCE, "-c:a", name, "-f", "null", "-"]
    res = await run(args, timeout=20)
    if res.returncode == 0:
        return True, None
    lines = [ln for ln in res.stderr.strip().splitlines() if ln.strip()]
    return False, (lines[-1] if lines else f"exit code {res.returncode}")[:300]


async def _make_samples(sample_dir: Path, available: set[str]) -> dict[str, Path]:
    sample_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for codec, encoder in _SAMPLE_ENCODERS.items():
        if encoder not in available:
            continue
        path = sample_dir / f"sample-{codec}.mkv"
        if not path.exists() or path.stat().st_size == 0:
            res = await run([ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", "-y", *_TEST_SOURCE, "-frames:v", "10", "-pix_fmt", "yuv420p", "-c:v", encoder, str(path)], timeout=60)
            if res.returncode != 0:
                continue
        out[codec] = path
    return out


async def _test_decoder(backend: str, sample: Path, device: str | None) -> tuple[bool, str | None]:
    if backend == Backend.NVENC:
        pre = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    else:
        pre = ["-hwaccel", "vaapi", "-hwaccel_device", device or "/dev/dri/renderD128", "-hwaccel_output_format", "vaapi"]
    res = await run([ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "warning", *pre, "-i", str(sample), "-f", "null", "-"], timeout=30)
    text = res.stderr
    if res.returncode == 0 and not any(m in text for m in _HW_FAIL_MARKERS):
        return True, None
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return False, (lines[-1] if lines else f"exit code {res.returncode}")[:300]


def _storage() -> list[StorageInfo]:
    out = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        mp = part.mountpoint
        if mp in seen or mp.startswith(("/proc", "/sys", "/dev", "/etc")) or part.fstype in ("tmpfs", "devtmpfs", "overlay", "squashfs"):
            continue
        try:
            usage = psutil.disk_usage(mp)
        except OSError:
            continue
        seen.add(mp)
        out.append(StorageInfo(path=mp, total_bytes=usage.total, free_bytes=usage.free))
    return out[:10]


def _recommend(caps: NodeCapabilities) -> int:
    hw = [e for e in caps.encoders if e.verified and e.backend != Backend.CPU]
    if hw:
        return 2  # one GPU typically saturates around 2 simultaneous encodes; more rarely helps
    return max(1, min(3, caps.cpu_threads // 12))


async def detect(state_dir: Path) -> NodeCapabilities:
    started = time.monotonic()
    caps = NodeCapabilities(
        cpu_model=_cpu_model(),
        cpu_threads=os.cpu_count() or 1,
        ram_total_mb=psutil.virtual_memory().total // (1024 * 1024),
        os=_os_name(),
        hostname=socket.gethostname(),
        storage=_storage(),
    )
    ver = await run([ffmpeg_path(), "-hide_banner", "-version"], timeout=15)
    if ver.returncode != 0:
        caps.notes.append("FFmpeg was not found in this container. Transcoding is unavailable.")
        caps.detected_at = time.time()
        return caps
    caps.ffmpeg_version = ver.stdout.splitlines()[0] if ver.stdout else None
    caps.engines = {"ffmpeg": True, "handbrake": False}

    available = await _ffmpeg_encoders()
    nvidia = await _nvidia_gpus()
    dri = await _dri_gpus(len(nvidia))
    caps.gpus = nvidia + dri
    intel = next((g for g in dri if g.vendor == "intel"), None)
    render = intel or (dri[0] if dri else None)
    caps.render_device = render.device if render else None

    for name, (codec, backend) in ENCODER_INFO.items():
        if name not in available:
            continue
        if backend == Backend.NVENC and not nvidia and not os.path.exists("/dev/nvidia0"):
            caps.encoders.append(EncoderCapability(name=name, codec=codec.value, backend=backend.value, verified=False, error="No NVIDIA GPU visible in the container"))
            continue
        if backend in (Backend.VAAPI, Backend.QSV) and not caps.render_device:
            caps.encoders.append(EncoderCapability(name=name, codec=codec.value, backend=backend.value, verified=False, error="No /dev/dri render device in the container"))
            continue
        if backend == Backend.QSV and not intel:
            caps.encoders.append(EncoderCapability(name=name, codec=codec.value, backend=backend.value, verified=False, error="Quick Sync needs an Intel GPU"))
            continue
        if backend == Backend.AMF and not any(g.vendor == "amd" for g in dri):
            caps.encoders.append(EncoderCapability(name=name, codec=codec.value, backend=backend.value, verified=False, error="AMF needs an AMD GPU (on Linux, AMD GPUs are used through VA-API)"))
            continue
        ok, err = await _test_encoder(name, backend.value, caps.render_device)
        caps.encoders.append(EncoderCapability(name=name, codec=codec.value, backend=backend.value, verified=ok, error=err))

    caps.audio_encoders = []
    for codec, name in AUDIO_ENCODERS.items():
        if name not in available:
            caps.audio_encoders.append(AudioEncoderCapability(name=name, codec=codec, verified=False, error="Not included in this FFmpeg build"))
            continue
        ok, err = await _test_audio_encoder(name)
        caps.audio_encoders.append(AudioEncoderCapability(name=name, codec=codec, verified=ok, error=err))

    if nvidia or caps.render_device:
        samples = await _make_samples(state_dir / "samples", available)
        backends = ([Backend.NVENC] if nvidia else []) + ([Backend.VAAPI] if caps.render_device else [])
        for backend in backends:
            for codec, path in samples.items():
                ok, err = await _test_decoder(backend.value, path, caps.render_device)
                caps.decoders.append(DecoderCapability(backend=backend.value, codec=codec, verified=ok, error=err))

    if not caps.gpus:
        caps.notes.append("No GPU is visible inside this container. CPU encoding works; see docs/gpu.md to enable hardware acceleration.")
    elif not any(e.verified and e.backend != Backend.CPU for e in caps.encoders):
        caps.notes.append("A GPU is visible but no hardware encoder passed its test. Check drivers and device permissions (docs/troubleshooting.md).")
    caps.recommended_concurrency = _recommend(caps)
    caps.detected_at = time.time()
    log.info(
        "Capability detection finished in %.1fs: %d verified encoders (%s)",
        time.monotonic() - started,
        len(caps.verified_encoders()),
        ", ".join(sorted(caps.verified_encoders())),
    )
    return caps
