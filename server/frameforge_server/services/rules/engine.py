"""Evaluate ordered rules against a file and decide what (if anything) should happen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from frameforge_shared.profile import ProfileSpec

from ...db.models import MediaFile, Profile, Rule
from .conditions import FIELDS, OPERATOR_LABELS, Condition, ConditionError, ConditionGroup, FileContext, HardwareSnapshot, apply_operator

PRIORITY_NAMES = {0: "Background", 1: "Low", 2: "Normal", 3: "High", 4: "Critical"}


@dataclass
class TraceItem:
    label: str
    passed: bool
    actual: str | None = None
    depth: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "passed": self.passed, "actual": self.actual, "depth": self.depth}


@dataclass
class Decision:
    action: Literal["transcode", "skip", "none"]
    reason: str
    rule: Rule | None = None
    profile: Profile | None = None
    priority: int = 2
    trace: list[TraceItem] = field(default_factory=list)
    rule_traces: list[dict[str, Any]] = field(default_factory=list)


def _fmt_actual(value: Any, unit: str | None) -> str:
    if value is None or value == []:
        return "unknown"
    if isinstance(value, float):
        value = f"{value:.1f}".rstrip("0").rstrip(".")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return f"{value} {unit}" if unit and unit != "p" else (f"{value}p" if unit == "p" else str(value))


def _fmt_value(value: Any) -> str:
    if isinstance(value, list):
        return " – ".join(str(v) for v in value) if len(value) == 2 else ", ".join(str(v) for v in value)
    return str(value)


def evaluate_tree(tree: ConditionGroup, ctx: FileContext, trace: list[TraceItem] | None = None, depth: int = 0) -> bool:
    results: list[bool] = []
    for child in tree.children:
        if isinstance(child, ConditionGroup):
            if trace is not None:
                trace.append(TraceItem({"all": "ALL of", "any": "ANY of", "none": "NONE of"}[child.op], True, None, depth))
                marker = len(trace) - 1
            ok = evaluate_tree(child, ctx, trace, depth + 1)
            if trace is not None:
                trace[marker].passed = ok
            results.append(ok)
            continue
        fd = FIELDS.get(child.field)
        if fd is None:
            results.append(False)
            if trace is not None:
                trace.append(TraceItem(f"Unknown field {child.field}", False, None, depth))
            continue
        actual = fd.getter(ctx)
        try:
            ok = apply_operator(fd, actual, child.operator, child.value)
        except ConditionError:
            ok = False
        results.append(ok)
        if trace is not None:
            value_txt = "" if fd.kind == "bool" else f" {_fmt_value(child.value)}{(' ' + fd.unit) if fd.unit and fd.unit != 'p' else ('p' if fd.unit == 'p' else '')}"
            trace.append(TraceItem(f"{fd.label} {OPERATOR_LABELS.get(child.operator, child.operator)}{value_txt}", ok, _fmt_actual(actual, fd.unit), depth))
    if not tree.children:
        return True
    if tree.op == "all":
        return all(results)
    if tree.op == "any":
        return any(results)
    return not any(results)


def build_context(file: MediaFile, hardware: HardwareSnapshot | None = None) -> FileContext:
    m = file.meta
    ctx = FileContext(
        path=file.path,
        filename=file.filename,
        extension=file.extension,
        size=file.size,
        mtime=file.mtime,
        hardware=hardware or HardwareSnapshot(),
    )
    if m is not None:
        ctx.container = m.container
        ctx.video_codec = m.video_codec
        ctx.audio_codecs = list(m.audio_codecs or [])
        ctx.width = m.width
        ctx.height = m.height
        ctx.short_side = m.short_side
        ctx.fps = m.fps
        ctx.bit_depth = m.bit_depth
        ctx.hdr_format = m.hdr_format
        ctx.bitrate = m.bitrate
        ctx.duration = m.duration
        ctx.creation_time = m.creation_time
        ctx.audio_count = m.audio_count
        ctx.subtitle_count = m.subtitle_count
    return ctx


def parse_tree(doc: dict[str, Any] | None) -> ConditionGroup:
    if not doc:
        return ConditionGroup()
    return ConditionGroup.model_validate(doc)


def _already_in_target(file: MediaFile, spec: ProfileSpec) -> bool:
    m = file.meta
    if m is None or spec.is_remux:
        return False
    if m.video_codec != spec.video_codec.value:
        return False
    # Same codec: only worth it if the profile also shrinks resolution or frame rate.
    shrinks_res = bool(spec.max_resolution and m.short_side and m.short_side > spec.max_resolution)
    shrinks_fps = bool(spec.max_fps and m.fps and m.fps > spec.max_fps + 0.5)
    return not (shrinks_res or shrinks_fps)


def decide(
    file: MediaFile,
    rules: list[Rule],
    ctx: FileContext,
    *,
    has_active_job: bool = False,
    failed_profile_ids: set[int] | None = None,
    with_traces: bool = False,
) -> Decision:
    """Pick the first enabled rule that matches ``file`` and apply safety guards."""
    if file.ignored:
        return Decision("none", "Ignored by you")
    if file.status in ("missing", "error", "new"):
        return Decision("none", {"missing": "File is missing", "error": "File could not be analyzed", "new": "Not analyzed yet"}[file.status])
    if has_active_job:
        return Decision("none", "Already in the queue")
    if getattr(file, "role", None) == "kept_original":
        return Decision("none", "Original kept after conversion; rules only handle its converted copy")

    traces: list[dict[str, Any]] = []
    for rule in sorted(rules, key=lambda r: (r.position, r.id)):
        if not rule.enabled:
            continue
        if rule.library_id is not None and rule.library_id != file.library_id:
            continue
        trace: list[TraceItem] = []
        try:
            tree = parse_tree(rule.conditions)
        except ValueError:
            continue
        matched = evaluate_tree(tree, ctx, trace)
        if with_traces:
            traces.append({"rule_id": rule.id, "rule_name": rule.name, "matched": matched, "trace": [t.as_dict() for t in trace]})
        if not matched:
            continue

        if rule.action == "skip":
            return Decision("skip", f"Rule “{rule.name}” says leave it alone", rule=rule, trace=trace, rule_traces=traces)
        profile = rule.profile
        if profile is None:
            return Decision("none", f"Rule “{rule.name}” has no profile", rule=rule, trace=trace, rule_traces=traces)
        spec = ProfileSpec.model_validate(profile.spec)
        tag = file.meta.frameforge_tag if file.meta else None
        if file.processed_profile_id == profile.id or (tag and f"profile={profile.id};" in tag + ";"):
            return Decision("skip", f"Already processed with “{profile.name}”", rule=rule, profile=profile, trace=trace, rule_traces=traces)
        if rule.skip_if_target_codec and _already_in_target(file, spec):
            return Decision("skip", f"Already {spec.target_label()}; nothing to gain", rule=rule, profile=profile, trace=trace, rule_traces=traces)
        if failed_profile_ids and profile.id in failed_profile_ids:
            return Decision("skip", f"A previous “{profile.name}” attempt failed; retry it from the job page", rule=rule, profile=profile, trace=trace, rule_traces=traces)
        return Decision(
            "transcode",
            f"Rule “{rule.name}” → {profile.name} ({PRIORITY_NAMES.get(rule.priority, rule.priority)} priority)",
            rule=rule,
            profile=profile,
            priority=rule.priority,
            trace=trace,
            rule_traces=traces,
        )
    return Decision("none", "No rule matches", rule_traces=traces)


def make_condition(field_key: str, operator: str, value: Any) -> dict[str, Any]:
    return Condition(field=field_key, operator=operator, value=value).model_dump()
