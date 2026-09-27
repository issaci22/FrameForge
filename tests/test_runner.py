"""Node job runner end to end with real FFmpeg: preflight refusals and what a result reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from frameforge_node.runner import JobRunner, RunningJob
from frameforge_shared import protocol as P
from frameforge_shared.fingerprint import fingerprint_file
from frameforge_shared.profile import ProfileSpec

from .conftest import make_clip, needs_ffmpeg

pytestmark = needs_ffmpeg

X264 = P.EncoderChoice(codec="h264", backend="cpu", encoder="libx264")
TINY = P.ValidationThresholds(min_output_bytes=1, max_size_ratio=None)


class Recorder:
    def __init__(self) -> None:
        self.messages: list[tuple[str, Any]] = []

    async def send(self, msg_type: str, data: Any) -> None:
        self.messages.append((msg_type, data))

    async def send_result(self, result: P.JobResult) -> None:  # unused: tests call _run directly
        self.messages.append(("result", result))

    def stages(self) -> list[str]:
        return [d.stage for t, d in self.messages if t == P.NODE_JOB_STAGE]


def assignment(src: Path, plan: P.FinalizePlan) -> P.JobAssignment:
    return P.JobAssignment(
        job_id=1,
        source_path=str(src),
        profile=ProfileSpec(container="mkv", video_codec="h264", speed="fast"),
        encoder=X264,
        plan=plan,
        thresholds=TINY,
        frameforge_tag="job=1;profile=1;v=1",
    )


async def test_an_existing_output_fails_before_encoding(tmp_path: Path) -> None:
    (tmp_path / "vods").mkdir()
    src = make_clip(tmp_path / "vods" / "stream.mp4")
    before = src.read_bytes()
    out = tmp_path / "out"
    out.mkdir()
    (out / "stream.mkv").write_bytes(b"EARLIER OUTPUT")
    plan = P.FinalizePlan(source=str(src), temp_output=str(out / ".frameforge-tmp" / "job-1.mkv"), final_output=str(out / "stream.mkv"), original_action="keep")
    rec = Recorder()
    result = await JobRunner(rec.send, rec.send_result)._run(RunningJob(1), assignment(src, plan))
    assert result.status == "failed"
    assert result.diagnosis is not None and result.diagnosis.code == "output_conflict"
    assert "transcoding" not in rec.stages(), "FFmpeg must not run for a plan that can't finish"
    assert (out / "stream.mkv").read_bytes() == b"EARLIER OUTPUT"
    assert src.read_bytes() == before
    assert not (out / ".frameforge-tmp").exists()


@pytest.mark.parametrize("action", ["keep", "backup", "delete"])
async def test_result_reports_where_the_original_is(tmp_path: Path, action: str) -> None:
    vods = tmp_path / "vods"
    vods.mkdir()
    src = make_clip(vods / "stream.mp4")
    fp = fingerprint_file(str(src))
    final = vods / ("stream.ff.mkv" if action == "keep" else "stream.mkv")
    backup = vods / ".frameforge-originals" / "stream.mp4"
    plan = P.FinalizePlan(
        source=str(src),
        temp_output=str(vods / ".frameforge-tmp" / "job-1.mkv"),
        final_output=str(final),
        original_action=action,  # type: ignore[arg-type]
        backup_path=str(backup) if action == "backup" else None,
    )
    rec = Recorder()
    result = await JobRunner(rec.send, rec.send_result)._run(RunningJob(1), assignment(src, plan))
    assert result.status == "completed", result.diagnosis
    assert result.final_path == str(final)
    if action == "delete":
        assert result.original_disposition == "deleted"
        assert result.original_path is None and not src.exists()
    else:
        where = src if action == "keep" else backup
        assert result.original_disposition == ("kept" if action == "keep" else "backed_up")
        assert result.original_path == str(where)
        assert result.original_fingerprint == fp
        assert result.original_size == where.stat().st_size
