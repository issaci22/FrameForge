"""Check a finished output before anything touches the original."""

from __future__ import annotations

import os

from frameforge_shared.media import MediaInfo
from frameforge_shared.probe import DECODE_CHECK_SECONDS, ProbeError, decode_check, probe_file
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import ValidationCheck, ValidationReport, ValidationThresholds

SIZE_CHECK = "Smaller than source"


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


async def validate(source: MediaInfo, source_size: int, output_path: str, thresholds: ValidationThresholds, spec: ProfileSpec) -> tuple[ValidationReport, MediaInfo | None]:
    checks: list[ValidationCheck] = []

    def add(name: str, passed: bool, detail: str, severity: str = "error") -> None:
        checks.append(ValidationCheck(name=name, passed=passed, detail=detail, severity=severity))  # type: ignore[arg-type]

    if not os.path.isfile(output_path):
        add("Output exists", False, "FFmpeg finished but no output file was written")
        return ValidationReport(passed=False, checks=checks), None
    size = os.path.getsize(output_path)
    add("Output size", size >= thresholds.min_output_bytes, f"{_mb(size)} (minimum {_mb(thresholds.min_output_bytes)})")

    try:
        out = await probe_file(output_path)
        add("Readable by ffprobe", True, f"{out.container.upper()}, {out.summary()}")
    except ProbeError as exc:
        add("Readable by ffprobe", False, str(exc))
        return ValidationReport(passed=False, checks=checks), None

    add("Video stream present", out.video is not None, out.video.codec if out.video else "no video stream")

    if source.audio and thresholds.require_audio_if_source_has_audio:
        add("Audio present", len(out.audio) > 0, f"{len(out.audio)} of {len(source.audio)} track(s)")
        if 0 < len(out.audio) < len(source.audio):
            add("All audio tracks kept", False, f"{len(out.audio)} of {len(source.audio)} audio tracks in the output", severity="warning")

    if source.duration > 0:
        diff_pct = abs(out.duration - source.duration) / source.duration * 100
        add(
            "Duration matches",
            diff_pct <= thresholds.duration_tolerance_pct,
            f"{out.duration:.1f}s vs {source.duration:.1f}s ({diff_pct:.2f}% difference, limit {thresholds.duration_tolerance_pct}%)",
        )

    if thresholds.max_size_ratio and source_size > 0 and not spec.is_remux:
        ratio = size / source_size
        ok = ratio <= thresholds.max_size_ratio
        add(
            SIZE_CHECK,
            ok,
            f"Output is {ratio * 100:.1f}% of the original ({_mb(size)} vs {_mb(source_size)}, limit {thresholds.max_size_ratio * 100:.0f}%)",
            severity="error" if thresholds.fail_if_larger else "warning",
        )

    for label, from_end in (("Start decodes cleanly", False), ("End decodes cleanly", True)):
        err = await decode_check(output_path, from_end)
        add(label, err is None, err or f"Decoded {DECODE_CHECK_SECONDS}s without errors")

    passed = all(c.passed for c in checks if c.severity == "error")
    return ValidationReport(passed=passed, checks=checks), out
