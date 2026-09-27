"""Output validation before anything touches the original (validator.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from frameforge_node import validator
from frameforge_shared.media import AudioStream, MediaInfo
from frameforge_shared.probe import ProbeError
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import ValidationCheck, ValidationReport, ValidationThresholds

from .conftest import make_clip, make_media, needs_ffmpeg

MB = 1024 * 1024
SOURCE_SIZE = 100 * MB


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Replace ffprobe/ffmpeg calls. Tests set ``probe`` (MediaInfo or exception) and ``decode_errors``."""

    class Tools:
        probe: MediaInfo | Exception = make_media(duration=600.0)
        decode_errors: dict[bool, str] = {}  # from_end -> error

    async def probe_file(path: str) -> MediaInfo:
        if isinstance(Tools.probe, Exception):
            raise Tools.probe
        return Tools.probe

    async def decode_probe(path: str, from_end: bool) -> str | None:
        return Tools.decode_errors.get(from_end)

    monkeypatch.setattr(validator, "probe_file", probe_file)
    monkeypatch.setattr(validator, "decode_check", decode_probe)
    return Tools


def output(tmp_path: Path, size: int = 40 * MB) -> str:
    p = tmp_path / "out.mkv"
    with open(p, "wb") as fh:
        fh.truncate(size)  # sparse: size without the disk cost
    return str(p)


async def run(path: str, source: MediaInfo | None = None, spec: ProfileSpec | None = None, **thresholds: object) -> ValidationReport:
    report, _ = await validator.validate(source or make_media(duration=600.0), SOURCE_SIZE, path, ValidationThresholds(**thresholds), spec or ProfileSpec())
    return report


def check(report: ValidationReport, name: str):  # noqa: ANN201
    return next(c for c in report.checks if c.name == name)


async def test_good_output_passes(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path))
    assert report.passed, [c for c in report.checks if not c.passed]
    assert {c.name for c in report.checks} >= {"Output size", "Readable by ffprobe", "Video stream present", "Audio present", "Duration matches", "Smaller than source", "Start decodes cleanly", "End decodes cleanly"}


async def test_missing_output_fails(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(str(tmp_path / "nope.mkv"))
    assert not report.passed
    assert check(report, "Output exists").passed is False


async def test_tiny_output_fails(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=1000))
    assert not report.passed
    assert not check(report, "Output size").passed


async def test_unreadable_output_fails_and_stops(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    fake_tools.probe = ProbeError("Invalid data found when processing input")
    report = await run(output(tmp_path))
    assert not report.passed
    assert "Invalid data" in check(report, "Readable by ffprobe").detail
    assert not any(c.name == "Duration matches" for c in report.checks)


async def test_no_video_stream_fails(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    media = make_media(duration=600.0)
    media.video = None
    fake_tools.probe = media
    assert not (await run(output(tmp_path))).passed


async def test_lost_audio_fails(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    fake_tools.probe = make_media(duration=600.0, audio=[])
    report = await run(output(tmp_path))
    assert not report.passed
    assert not check(report, "Audio present").passed


async def test_audio_check_can_be_disabled(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    fake_tools.probe = make_media(duration=600.0, audio=[])
    assert (await run(output(tmp_path), require_audio_if_source_has_audio=False)).passed


async def test_dropped_audio_track_is_a_warning(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    source = make_media(duration=600.0, audio=[AudioStream(index=1, codec="aac"), AudioStream(index=2, codec="aac")])
    report = await run(output(tmp_path), source)
    assert report.passed
    c = check(report, "All audio tracks kept")
    assert not c.passed and c.severity == "warning"


@pytest.mark.parametrize(("out_duration", "passes"), [(600.0, True), (611.9, True), (612.1, False), (590.0, True), (580.0, False), (300.0, False)])
async def test_duration_tolerance(tmp_path: Path, fake_tools, out_duration: float, passes: bool) -> None:  # noqa: ANN001
    fake_tools.probe = make_media(duration=out_duration)
    report = await run(output(tmp_path))  # default tolerance 2% of 600s = 12s
    assert check(report, "Duration matches").passed is passes
    assert report.passed is passes


@pytest.mark.parametrize(("size", "passes"), [(SOURCE_SIZE - 1, True), (SOURCE_SIZE, True), (SOURCE_SIZE + 1, False), (101 * MB, False), (167 * MB, False)])
async def test_larger_output_fails_by_default(tmp_path: Path, fake_tools, size: int, passes: bool) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=size))
    c = check(report, "Smaller than source")
    assert c.passed is passes and c.severity == "error"
    assert report.passed is passes


async def test_larger_output_detail_shows_ratio_and_limit(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=SOURCE_SIZE + MB // 2))
    assert check(report, "Smaller than source").detail == "Output is 100.5% of the original (100.5 MB vs 100.0 MB, limit 100%)"


async def test_larger_output_can_be_allowed_per_library(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=120 * MB), fail_if_larger=False)
    c = check(report, "Smaller than source")
    assert not c.passed and c.severity == "warning"
    assert report.passed


async def test_size_limit_can_be_raised(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    assert (await run(output(tmp_path, size=120 * MB), max_size_ratio=1.25)).passed


async def test_size_check_can_be_disabled(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=300 * MB), max_size_ratio=None)
    assert report.passed
    assert not any(c.name == "Smaller than source" for c in report.checks)


async def test_remux_skips_size_comparison(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    report = await run(output(tmp_path, size=120 * MB), spec=ProfileSpec(video_codec="copy"))
    assert report.passed
    assert not any(c.name == "Smaller than source" for c in report.checks)


async def test_truncated_end_fails(tmp_path: Path, fake_tools) -> None:  # noqa: ANN001
    fake_tools.decode_errors = {True: "Error while decoding stream #0:0: Invalid data found"}
    report = await run(output(tmp_path))
    assert not report.passed
    assert not check(report, "End decodes cleanly").passed
    assert check(report, "Start decodes cleanly").passed


def test_defaults_reject_larger_outputs() -> None:
    t = ValidationThresholds()
    assert t.fail_if_larger is True and t.max_size_ratio == 1.0


def test_size_only_rejection_gets_its_own_diagnosis() -> None:
    from frameforge_node.runner import _validation_diagnosis

    size = ValidationCheck(name="Smaller than source", passed=False, detail="Output is 167.0% of the original")
    diag = _validation_diagnosis([size])
    assert diag.code == "output_larger"
    assert "left the original untouched" in diag.explanation and "167.0%" in diag.explanation

    other = ValidationCheck(name="Duration matches", passed=False, detail="300.0s vs 600.0s")
    assert _validation_diagnosis([size, other]).code == "validation_failed"


# ---------------------------------------------------------------------------
# Real ffmpeg round trips
# ---------------------------------------------------------------------------


@needs_ffmpeg
async def test_real_clip_validates(tmp_path: Path) -> None:
    from frameforge_shared.probe import probe_file

    clip = make_clip(tmp_path / "clip.mkv", seconds=3)
    source = await probe_file(str(clip))
    report, out = await validator.validate(source, clip.stat().st_size, str(clip), ValidationThresholds(min_output_bytes=1), ProfileSpec(video_codec="copy"))
    assert report.passed, [c for c in report.checks if not c.passed]
    assert out is not None and out.video is not None


@needs_ffmpeg
async def test_real_truncated_file_fails(tmp_path: Path) -> None:
    from frameforge_shared.probe import probe_file

    clip = make_clip(tmp_path / "clip.mp4", seconds=3)
    source = await probe_file(str(clip))
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(clip.read_bytes()[: clip.stat().st_size // 2])  # MP4 cut mid-file: no moov atom
    report, _ = await validator.validate(source, clip.stat().st_size, str(broken), ValidationThresholds(min_output_bytes=1), ProfileSpec())
    assert not report.passed
