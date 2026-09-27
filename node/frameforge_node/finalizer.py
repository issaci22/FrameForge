"""Safely put a validated output in place and deal with the original.

Invariants (see AGENTS.md §3):
* The original is never deleted before the output sits at its final path AND re-probes cleanly.
* Every state is recoverable from the filesystem alone (``recover``).
* Files FrameForge didn't create are never overwritten.

All file operations use the node's local paths. Messages meant for users pass paths through ``show``,
which the runner sets to translate them back to server paths.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from frameforge_shared.errors import diagnosis
from frameforge_shared.protocol import Diagnosis, FinalizePlan

log = logging.getLogger(__name__)

StepCallback = Callable[[str], Awaitable[None]]
Verifier = Callable[[str], Awaitable[bool]]
PathDisplay = Callable[[str], str]
# What happened to the original. Retention (server side) needs to know where it really is.
OriginalDisposition = Literal["deleted", "backed_up", "kept", "rescued", "unknown"]


def _as_is(path: str) -> str:
    return path


class FinalizeError(Exception):
    def __init__(self, diag: Diagnosis) -> None:
        super().__init__(diag.title)
        self.diagnosis = diag


@dataclass
class FinalizeOutcome:
    final_path: str
    output_size: int
    warnings: list[str] = field(default_factory=list)
    original_disposition: OriginalDisposition = "unknown"
    original_path: str | None = None  # local path of the original afterwards; None when deleted or unknown


@dataclass
class _OriginalResult:
    warnings: list[str]
    disposition: OriginalDisposition
    path: str | None


def hold_path(plan: FinalizePlan) -> str:
    """Where an original is parked while its replacement moves in (same filesystem, hidden dir)."""
    return plan.temp_output + ".original"


def _journal(plan: FinalizePlan, step: str, **extra: Any) -> None:
    try:
        with open(plan.temp_output + ".journal", "w", encoding="utf-8") as fh:
            json.dump({"step": step, "at": time.time(), "plan": plan.model_dump(), **extra}, fh)
    except OSError:
        log.warning("Could not write finalize journal for %s", plan.temp_output)


def _read_journal(plan: FinalizePlan) -> dict[str, Any]:
    try:
        with open(plan.temp_output + ".journal", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _disposition_from_journal(plan: FinalizePlan) -> tuple[OriginalDisposition, str | None]:
    """After a crash the original may already be gone from the source path. Only trust what was journaled."""
    entry = _read_journal(plan)
    if entry.get("step") == "backing_up_original":
        dest = entry.get("backup_dest")
        if isinstance(dest, str) and os.path.isfile(dest):
            return "backed_up", dest
    if entry.get("step") == "deleting_original":
        return "deleted", None
    return "unknown", None


def _cleanup(plan: FinalizePlan) -> None:
    for p in (plan.temp_output + ".journal",):
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(os.path.dirname(plan.temp_output))  # only succeeds when empty
    except OSError:
        pass


def _same(a: str, b: str) -> bool:
    return os.path.normpath(a) == os.path.normpath(b)


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _place_no_clobber(src: str, dst: str) -> None:
    """Move src → dst without ever replacing an existing dst."""
    if os.path.exists(dst):
        raise FileExistsError(dst)
    try:
        os.link(src, dst)  # atomic no-clobber where hard links are supported
        os.remove(src)
    except FileExistsError:
        raise
    except OSError:
        if os.path.exists(dst):
            raise FileExistsError(dst) from None
        os.replace(src, dst)


def _move_verified(src: str, dst: str) -> None:
    """Move a file, possibly across filesystems, verifying the copy before removing the source."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        _place_no_clobber(src, dst)
        return
    except FileExistsError:
        raise
    except OSError:
        pass  # cross-device: fall through to copy
    size = os.path.getsize(src)
    tmp = dst + ".frameforge-partial"
    shutil.copy2(src, tmp)
    if os.path.getsize(tmp) != size:
        os.remove(tmp)
        raise OSError("the backup copy was incomplete")
    os.replace(tmp, dst)
    os.remove(src)


def _err(code: str, title: str, explanation: str, *causes: str, technical: str | None = None) -> FinalizeError:
    return FinalizeError(diagnosis(code, title, explanation, *causes, technical=technical))


async def _handle_original(plan: FinalizePlan, original: str, on_step: StepCallback, show: PathDisplay) -> _OriginalResult:
    """Delete / back up / keep the original. Failures here never lose data; they become warnings."""
    warnings: list[str] = []
    same = _same(plan.final_output, plan.source)
    if plan.original_action == "keep":
        return _OriginalResult(warnings, "kept", original)
    try:
        if plan.original_action == "delete":
            _journal(plan, "deleting_original")
            await on_step("Removing the original (output verified)")
            os.remove(original)
            return _OriginalResult(warnings, "deleted", None)
        assert plan.backup_path
        dest = _unique(plan.backup_path)
        _journal(plan, "backing_up_original", backup_dest=dest)
        await on_step(f"Moving the original to {show(dest)}")
        _move_verified(original, dest)
        return _OriginalResult(warnings, "backed_up", dest)
    except OSError as exc:
        verb = "delete" if plan.original_action == "delete" else "back up"
        if same and os.path.exists(original):
            stem, ext = os.path.splitext(plan.source)
            rescue = _unique(f"{stem}.original{ext}")
            os.replace(original, rescue)
            warnings.append(f"Couldn't {verb} the original ({exc.strerror or exc}); it was kept as {show(rescue)}")
            return _OriginalResult(warnings, "rescued", rescue)
        warnings.append(f"Couldn't {verb} the original ({exc.strerror or exc}); it is still at {show(original)}")
        return _OriginalResult(warnings, "kept", original)


def preflight(plan: FinalizePlan, show: PathDisplay = _as_is) -> None:
    """Refuse plans that can never finish, before any encoding. ``finalize`` repeats these checks."""
    same = _same(plan.final_output, plan.source)
    if same and plan.original_action == "keep":
        raise _err("plan_conflict", "Output would overwrite the original", "This library keeps originals, but the output path is the same as the source.", "Choose a different output folder for this library")
    if plan.original_action == "backup" and not plan.backup_path:
        raise _err("plan_conflict", "No backup folder configured", "The library is set to back up originals but has no backup folder.")
    if not same and os.path.exists(plan.final_output):
        raise _err(
            "output_conflict",
            "A file already exists at the output location",
            f"{show(plan.final_output)} already exists. FrameForge never overwrites files it didn't create.",
            "An earlier conversion of this file (for example by another aging stage) is already there",
            "A file with the same name but the target extension is already next to the source",
            "Rename or move that file, then retry the job",
        )


async def finalize(plan: FinalizePlan, on_step: StepCallback, verify: Verifier, show: PathDisplay = _as_is) -> FinalizeOutcome:
    """Run after validation passed. ``plan`` paths must already be mapped to local paths."""
    same = _same(plan.final_output, plan.source)
    if not os.path.isfile(plan.temp_output):
        raise _err("output_missing", "The transcoded file disappeared", "The temporary output vanished before it could be moved into place.", "Something else deleted files in the .frameforge-tmp folder")
    preflight(plan, show)
    expected = os.path.getsize(plan.temp_output)

    held = hold_path(plan)
    if same:
        _journal(plan, "holding_original")
        await on_step("Setting the original aside")
        os.replace(plan.source, held)
        try:
            _journal(plan, "placing_output")
            await on_step("Moving the new file into place")
            os.replace(plan.temp_output, plan.final_output)
        except OSError as exc:
            os.replace(held, plan.source)
            raise _err("finalize_failed", "Couldn't move the output into place", "The original has been restored untouched.", technical=str(exc)) from exc
    else:
        _journal(plan, "placing_output")
        await on_step("Moving the new file into place")
        try:
            _place_no_clobber(plan.temp_output, plan.final_output)
        except FileExistsError as exc:
            raise _err("output_conflict", "A file already exists at the output location", f"{show(plan.final_output)} appeared while finishing. Nothing was overwritten.") from exc
        except OSError as exc:
            raise _err("finalize_failed", "Couldn't move the output into place", "The original is untouched.", technical=str(exc)) from exc

    return await _verify_and_finish(plan, expected, on_step, verify, same, held, show)


async def _verify_and_finish(plan: FinalizePlan, expected: int | None, on_step: StepCallback, verify: Verifier, same: bool, held: str, show: PathDisplay) -> FinalizeOutcome:
    _journal(plan, "verifying")
    await on_step("Re-checking the file at its final location")
    size = os.path.getsize(plan.final_output) if os.path.exists(plan.final_output) else -1
    ok = size >= 0 and (expected is None or size == expected) and await verify(plan.final_output)
    if not ok:
        # Roll back: put the output back in temp and restore the original.
        if os.path.exists(plan.final_output):
            os.replace(plan.final_output, plan.temp_output)
        if same and os.path.exists(held):
            os.replace(held, plan.source)
        try:
            os.remove(plan.temp_output)
        except OSError:
            pass
        _cleanup(plan)
        raise _err("verify_after_move_failed", "The output failed its final check", "After moving the output into place it couldn't be read back correctly, so the change was rolled back and the original restored.", "Storage or network share errors while writing")

    original = await _handle_original(plan, held if same else plan.source, on_step, show)
    _cleanup(plan)
    return FinalizeOutcome(
        final_path=plan.final_output,
        output_size=size,
        warnings=original.warnings,
        original_disposition=original.disposition,
        original_path=original.path,
    )


async def recover(plan: FinalizePlan, on_step: StepCallback, verify: Verifier, show: PathDisplay = _as_is) -> FinalizeOutcome:
    """Resume or roll back an interrupted finalize, based purely on what's on disk."""
    same = _same(plan.final_output, plan.source)
    held = hold_path(plan)
    temp_exists = os.path.isfile(plan.temp_output)
    final_exists = os.path.isfile(plan.final_output)
    held_exists = os.path.isfile(held)
    await on_step(f"Recovering: temp={'yes' if temp_exists else 'no'}, final={'yes' if final_exists else 'no'}, held original={'yes' if held_exists else 'no'}")

    if same:
        if held_exists and final_exists:
            return await _verify_and_finish(plan, None, on_step, verify, same, held, show)
        if held_exists and not final_exists:
            if temp_exists:
                os.replace(plan.temp_output, plan.final_output)
                return await _verify_and_finish(plan, None, on_step, verify, same, held, show)
            os.replace(held, plan.source)
            _cleanup(plan)
            raise _err("recovered_rollback", "Finishing was interrupted; original restored", "The transcoded output was lost, so the original was put back exactly as it was.")
        if final_exists and not held_exists:
            if temp_exists:
                return await finalize(plan, on_step, verify, show)  # never started moving
            if await verify(plan.final_output):
                disposition, where = _disposition_from_journal(plan)
                _cleanup(plan)
                return FinalizeOutcome(final_path=plan.final_output, output_size=os.path.getsize(plan.final_output), original_disposition=disposition, original_path=where)
            _cleanup(plan)
            raise _err("recovered_untouched", "Finishing never started", "The original is untouched; the output was lost. The job will run again.")
        raise _err(
            "recover_inconsistent",
            "Couldn't recover this job automatically",
            f"Neither the original ({show(plan.source)}) nor its parked copy ({show(held)}) exists.",
            "Files were moved or deleted while the node was offline",
        )

    if final_exists and await verify(plan.final_output):
        if temp_exists and os.path.samefile(plan.temp_output, plan.final_output):
            os.remove(plan.temp_output)  # interrupted between link() and remove() in _place_no_clobber
        if os.path.exists(plan.source):
            original = await _handle_original(plan, plan.source, on_step, show)
        else:
            disposition, where = _disposition_from_journal(plan)
            original = _OriginalResult([], disposition, where)
        _cleanup(plan)
        return FinalizeOutcome(
            final_path=plan.final_output,
            output_size=os.path.getsize(plan.final_output),
            warnings=original.warnings,
            original_disposition=original.disposition,
            original_path=original.path,
        )
    if temp_exists:
        return await finalize(plan, on_step, verify, show)
    _cleanup(plan)
    raise _err("recovered_untouched", "Finishing never completed", "The original is untouched; the output was lost. The job will run again.")
