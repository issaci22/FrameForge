"""Live resource metrics. Values that can't be read are reported as None, never guessed."""

from __future__ import annotations

import glob
import os
import time
from pathlib import Path

import psutil

from frameforge_shared.protocol import GpuMetrics, NodeCapabilities, NodeMetrics
from frameforge_shared.tools import find_tool

from .proc import run


class MetricsSampler:
    def __init__(self, caps: NodeCapabilities) -> None:
        self.caps = caps
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()
        self._last_t = time.monotonic()
        self._smi = find_tool("nvidia-smi")
        self._smi_fields = "index,utilization.gpu,utilization.encoder,utilization.decoder,memory.used,memory.total,temperature.gpu"
        psutil.cpu_percent(interval=None)  # prime

    async def _nvidia(self) -> list[GpuMetrics]:
        if not self._smi or not any(g.vendor == "nvidia" for g in self.caps.gpus):
            return []
        res = await run([self._smi, f"--query-gpu={self._smi_fields}", "--format=csv,noheader,nounits"], timeout=5)
        if res.returncode != 0 and "utilization.encoder" in self._smi_fields:
            # Older drivers don't expose encoder/decoder utilization.
            self._smi_fields = "index,utilization.gpu,memory.used,memory.total,temperature.gpu"
            res = await run([self._smi, f"--query-gpu={self._smi_fields}", "--format=csv,noheader,nounits"], timeout=5)
        if res.returncode != 0:
            return []
        keys = self._smi_fields.split(",")
        out = []
        for line in res.stdout.strip().splitlines():
            vals = dict(zip(keys, (v.strip() for v in line.split(","))))

            def num(key: str) -> float | None:
                try:
                    return float(vals[key])
                except (KeyError, ValueError):
                    return None

            out.append(
                GpuMetrics(
                    index=int(num("index") or 0),
                    utilization=num("utilization.gpu"),
                    encoder_utilization=num("utilization.encoder"),
                    decoder_utilization=num("utilization.decoder"),
                    vram_used_mb=int(num("memory.used") or 0) if num("memory.used") is not None else None,
                    vram_total_mb=int(num("memory.total") or 0) if num("memory.total") is not None else None,
                    temperature_c=num("temperature.gpu"),
                )
            )
        return out

    def _dri(self) -> list[GpuMetrics]:
        out = []
        for gpu in self.caps.gpus:
            if gpu.vendor not in ("amd", "intel") or not gpu.device:
                continue
            node = os.path.basename(gpu.device)
            base = Path(f"/sys/class/drm/{node}/device")
            util = vram_used = vram_total = None
            try:
                util = float((base / "gpu_busy_percent").read_text().strip())
            except (OSError, ValueError):
                pass
            try:
                vram_used = int((base / "mem_info_vram_used").read_text().strip()) // (1024 * 1024)
                vram_total = int((base / "mem_info_vram_total").read_text().strip()) // (1024 * 1024)
            except (OSError, ValueError):
                pass
            temp = None
            for hw in glob.glob(str(base / "hwmon" / "hwmon*" / "temp1_input")):
                try:
                    temp = int(Path(hw).read_text().strip()) / 1000
                    break
                except (OSError, ValueError):
                    pass
            out.append(GpuMetrics(index=gpu.index, utilization=util, vram_used_mb=vram_used, vram_total_mb=vram_total, temperature_c=temp))
        return out

    async def sample(self) -> NodeMetrics:
        now = time.monotonic()
        dt = max(0.001, now - self._last_t)
        vm = psutil.virtual_memory()
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        read_bps = write_bps = rx_bps = tx_bps = None
        if disk and self._last_disk:
            read_bps = (disk.read_bytes - self._last_disk.read_bytes) / dt
            write_bps = (disk.write_bytes - self._last_disk.write_bytes) / dt
        if net and self._last_net:
            rx_bps = (net.bytes_recv - self._last_net.bytes_recv) / dt
            tx_bps = (net.bytes_sent - self._last_net.bytes_sent) / dt
        self._last_disk, self._last_net, self._last_t = disk, net, now
        try:
            load = os.getloadavg()[0]
        except (OSError, AttributeError):
            load = None
        return NodeMetrics(
            timestamp=time.time(),
            cpu_percent=psutil.cpu_percent(interval=None),
            ram_used_mb=(vm.total - vm.available) // (1024 * 1024),
            ram_total_mb=vm.total // (1024 * 1024),
            load_1m=load,
            gpus=(await self._nvidia()) + self._dri(),
            disk_read_bps=read_bps,
            disk_write_bps=write_bps,
            net_rx_bps=rx_bps,
            net_tx_bps=tx_bps,
        )
