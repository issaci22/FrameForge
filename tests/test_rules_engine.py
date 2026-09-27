"""Rule engine: condition operators, tree evaluation, decide() guards, aging policy stages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from frameforge_server.api.rules import AgingPolicyIn, AgingStage, build_aging_rules
from frameforge_server.db.models import MediaFile, MediaMetadata, Profile, Rule
from frameforge_server.services.rules.conditions import (
    FIELDS,
    Condition,
    ConditionError,
    ConditionGroup,
    FileContext,
    HardwareSnapshot,
    apply_operator,
    validate_tree,
)
from frameforge_server.services.rules.engine import build_context, decide, evaluate_tree, make_condition

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

HEVC_PROFILE = {"video_codec": "hevc", "quality": 70}
AV1_PROFILE = {"video_codec": "av1", "quality": 60}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def media_file(*, age_days: float = 100, codec: str = "h264", short_side: int = 1080, fps: float = 60.0, tag: str | None = None, **overrides: Any) -> MediaFile:
    f = MediaFile(
        id=overrides.pop("id", 1),
        library_id=overrides.pop("library_id", 1),
        path="/media/vods/2024/stream.mkv",
        relative_path="2024/stream.mkv",
        filename="stream.mkv",
        extension=".mkv",
        size=8 * 1024**3,
        mtime=NOW - timedelta(days=age_days),
        status=overrides.pop("status", "analyzed"),
        ignored=overrides.pop("ignored", False),
        processed_profile_id=overrides.pop("processed_profile_id", None),
    )
    f.meta = MediaMetadata(
        file_id=f.id,
        container="mkv",
        duration=3600.0,
        bitrate=18_000_000,
        video_codec=codec,
        width=short_side * 16 // 9,
        height=short_side,
        short_side=short_side,
        fps=fps,
        bit_depth=8,
        hdr_format=None,
        audio_codecs=["aac"],
        audio_count=1,
        subtitle_count=0,
        chapter_count=0,
        frameforge_tag=tag,
    )
    for k, v in overrides.items():
        setattr(f, k, v)
    return f


def profile(pid: int, spec: dict[str, Any], name: str | None = None) -> Profile:
    return Profile(id=pid, name=name or f"Profile {pid}", spec=spec)


def rule(rid: int, conditions: list[dict[str, Any]] | None = None, *, prof: Profile | None = None, action: str = "transcode", position: int = 0, op: str = "all", **kw: Any) -> Rule:
    r = Rule(
        id=rid,
        name=kw.pop("name", f"Rule {rid}"),
        position=position,
        enabled=kw.pop("enabled", True),
        library_id=kw.pop("library_id", None),
        conditions={"type": "group", "op": op, "children": conditions or []},
        action=action,
        profile_id=prof.id if prof else None,
        priority=kw.pop("priority", 2),
        skip_if_target_codec=kw.pop("skip_if_target_codec", True),
    )
    r.profile = prof
    return r


def ctx_for(f: MediaFile, **kw: Any) -> FileContext:
    c = build_context(f, kw.pop("hardware", None))
    c.now = NOW
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def cond(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return make_condition(field, op, value)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "value", "actual", "expected"),
    [
        ("gt", 30, 31, True),
        ("gt", 30, 30, False),
        ("gte", 30, 30, True),
        ("lt", 30, 29.9, True),
        ("lte", 30, 30, True),
        ("eq", 30, 30.0, True),
        ("neq", 30, 31, True),
        ("between", [10, 20], 10, True),
        ("between", "10,20", 20, True),
        ("between", [10, 20], 21, False),
    ],
)
def test_number_operators(op: str, value: Any, actual: float, expected: bool) -> None:
    assert apply_operator(FIELDS["file_age_days"], actual, op, value) is expected


def test_enum_is_case_insensitive_and_accepts_lists() -> None:
    fd = FIELDS["video_codec"]
    assert apply_operator(fd, "HEVC", "is", "hevc")
    assert apply_operator(fd, "h264", "in", ["hevc", "h264"])
    assert apply_operator(fd, "h264", "in", "hevc, h264")
    assert apply_operator(fd, "av1", "not_in", ["hevc", "h264"])


def test_enum_list_actual_matches_any_track() -> None:
    fd = FIELDS["audio_codec"]
    assert apply_operator(fd, ["aac", "pcm_s16le"], "is", "pcm_s16le")
    assert not apply_operator(fd, ["aac", "pcm_s16le"], "not_in", ["pcm_s16le"])


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("contains", "STREAM", True),
        ("not_contains", "shorts", True),
        ("starts_with", "/media/vods", True),
        ("ends_with", ".MKV", True),
        ("glob", "*/2024/*", True),
        ("matches", r"stream-\d{4}", False),
        ("matches", r"2024/str", True),
    ],
)
def test_text_operators(op: str, value: str, expected: bool) -> None:
    assert apply_operator(FIELDS["path"], "/media/vods/2024/stream.mkv", op, value) is expected


def test_unknown_values_never_satisfy_positive_comparisons() -> None:
    assert not apply_operator(FIELDS["frame_rate"], None, "gt", 0)
    assert not apply_operator(FIELDS["frame_rate"], None, "lt", 1000)
    assert not apply_operator(FIELDS["video_codec"], None, "is", "h264")
    assert apply_operator(FIELDS["video_codec"], None, "is_not", "h264")


def test_time_window_wraps_midnight() -> None:
    fd = FIELDS["time_of_day"]
    assert apply_operator(fd, "23:30", "between", ["23:00", "07:00"])
    assert apply_operator(fd, "03:00", "between", ["23:00", "07:00"])
    assert not apply_operator(fd, "07:00", "between", ["23:00", "07:00"])
    assert not apply_operator(fd, "12:00", "between", ["23:00", "07:00"])
    assert apply_operator(fd, "12:00", "between", ["09:00", "17:00"])


def test_bad_operator_for_field_is_rejected() -> None:
    with pytest.raises(ConditionError):
        apply_operator(FIELDS["video_codec"], "h264", "gt", 3)


def test_validate_tree_rejects_bad_rules_at_save_time() -> None:
    with pytest.raises(ConditionError, match="Unknown field"):
        validate_tree(ConditionGroup(children=[Condition(field="nope", operator="is", value=1)]))
    with pytest.raises(ConditionError, match="not a number"):
        validate_tree(ConditionGroup(children=[Condition(field="file_age_days", operator="gt", value="old")]))
    with pytest.raises(ConditionError, match="regular expression"):
        validate_tree(ConditionGroup(children=[Condition(field="path", operator="matches", value="(")]))
    validate_tree(ConditionGroup(children=[ConditionGroup(op="any", children=[Condition(field="resolution", operator="gte", value=1440)])]))


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


def test_age_uses_mtime_and_recording_age_prefers_creation_time() -> None:
    f = media_file(age_days=45)
    c = ctx_for(f)
    assert FIELDS["file_age_days"].getter(c) == pytest.approx(45)
    assert FIELDS["recorded_age_days"].getter(c) == pytest.approx(45)
    c.creation_time = NOW - timedelta(days=400)
    assert FIELDS["recorded_age_days"].getter(c) == pytest.approx(400)


def test_naive_mtime_is_treated_as_utc() -> None:
    c = ctx_for(media_file())
    c.mtime = (NOW - timedelta(days=2)).replace(tzinfo=None)
    assert FIELDS["file_age_days"].getter(c) == pytest.approx(2)


def test_resolution_is_short_side_and_orientation() -> None:
    f = media_file(short_side=1080)
    f.meta.width, f.meta.height = 1080, 1920
    c = ctx_for(f)
    assert FIELDS["resolution"].getter(c) == 1080
    assert FIELDS["orientation"].getter(c) == "vertical"


def test_hardware_fields() -> None:
    c = ctx_for(media_file(), hardware=HardwareSnapshot(online_nodes=2, hw_codecs={"hevc", "av1"}))
    assert apply_operator(FIELDS["hw_encoder_for"], FIELDS["hw_encoder_for"].getter(c), "is", "av1")
    assert FIELDS["online_nodes"].getter(c) == 2


# ---------------------------------------------------------------------------
# Tree evaluation
# ---------------------------------------------------------------------------


def test_all_any_none_groups() -> None:
    c = ctx_for(media_file(age_days=100, codec="h264"))
    yes = Condition(field="video_codec", operator="is", value="h264")
    no = Condition(field="video_codec", operator="is", value="av1")
    assert evaluate_tree(ConditionGroup(op="all", children=[yes, yes]), c)
    assert not evaluate_tree(ConditionGroup(op="all", children=[yes, no]), c)
    assert evaluate_tree(ConditionGroup(op="any", children=[no, yes]), c)
    assert evaluate_tree(ConditionGroup(op="none", children=[no, no]), c)
    assert not evaluate_tree(ConditionGroup(op="none", children=[no, yes]), c)
    assert evaluate_tree(ConditionGroup(), c), "an empty group matches everything"


def test_nested_groups_and_trace() -> None:
    c = ctx_for(media_file(age_days=100, codec="h264", short_side=1440))
    tree = ConditionGroup.model_validate(
        {
            "op": "all",
            "children": [
                cond("file_age_days", "gt", 30),
                {"type": "group", "op": "any", "children": [cond("resolution", "gte", 2160), cond("video_codec", "is", "h264")]},
            ],
        }
    )
    trace: list = []
    assert evaluate_tree(tree, c, trace)
    labels = [(t.label, t.passed, t.depth) for t in trace]
    assert labels[0] == ("File age is greater than 30 days", True, 0)
    assert labels[1] == ("ANY of", True, 0)
    assert labels[2][1:] == (False, 1)  # resolution ≥ 2160 fails inside the group
    assert trace[2].actual == "1440p"


def test_unknown_field_in_stored_tree_fails_closed() -> None:
    c = ctx_for(media_file())
    assert not evaluate_tree(ConditionGroup(children=[Condition(field="gone", operator="is", value=1)]), c)


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------


def test_first_matching_rule_by_position_wins() -> None:
    p1, p2 = profile(1, HEVC_PROFILE), profile(2, AV1_PROFILE)
    f = media_file(age_days=100)
    rules = [rule(2, [cond("file_age_days", "gt", 30)], prof=p2, position=5), rule(1, [cond("file_age_days", "gt", 30)], prof=p1, position=1)]
    d = decide(f, rules, ctx_for(f))
    assert d.action == "transcode" and d.profile is p1


def test_disabled_and_other_library_rules_are_ignored() -> None:
    p = profile(1, HEVC_PROFILE)
    f = media_file(library_id=1)
    rules = [rule(1, prof=p, enabled=False), rule(2, prof=p, library_id=2)]
    assert decide(f, rules, ctx_for(f)).action == "none"


def test_skip_rule_protects_files() -> None:
    p = profile(1, HEVC_PROFILE)
    f = media_file(age_days=3)
    rules = [rule(1, [cond("file_age_days", "lt", 30)], action="skip", position=0), rule(2, prof=p, position=1)]
    d = decide(f, rules, ctx_for(f))
    assert d.action == "skip"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"ignored": True}, "Ignored"),
        ({"status": "missing"}, "missing"),
        ({"status": "error"}, "could not be analyzed"),
        ({"status": "new"}, "Not analyzed"),
    ],
)
def test_files_that_must_not_be_touched(kwargs: dict[str, Any], reason: str) -> None:
    f = media_file(**kwargs)
    d = decide(f, [rule(1, prof=profile(1, HEVC_PROFILE))], ctx_for(f))
    assert d.action == "none" and reason in d.reason


def test_active_job_blocks_a_second_job() -> None:
    f = media_file()
    assert decide(f, [rule(1, prof=profile(1, HEVC_PROFILE))], ctx_for(f), has_active_job=True).reason == "Already in the queue"


def test_reprocessing_guard_by_processed_profile_id() -> None:
    p = profile(7, HEVC_PROFILE)
    f = media_file(processed_profile_id=7, codec="h264")
    d = decide(f, [rule(1, prof=p)], ctx_for(f))
    assert d.action == "skip" and "Already processed" in d.reason


def test_reprocessing_guard_by_frameforge_tag() -> None:
    p = profile(7, HEVC_PROFILE)
    f = media_file(codec="h264", tag="job=12;profile=7;v=1")
    assert decide(f, [rule(1, prof=p)], ctx_for(f)).action == "skip"
    # profile=7 must not match profile=77
    f2 = media_file(codec="h264", tag="job=12;profile=77;v=1")
    assert decide(f2, [rule(1, prof=p)], ctx_for(f2)).action == "transcode"


def test_tag_guard_matches_last_field_too() -> None:
    p = profile(7, HEVC_PROFILE)
    f = media_file(codec="h264", tag="job=12;profile=7")
    assert decide(f, [rule(1, prof=p)], ctx_for(f)).action == "skip"


def test_aging_stage_with_a_different_profile_still_applies() -> None:
    # H.265 output from stage 1 is re-encoded to AV1 by stage 2, by design.
    stage2 = profile(8, AV1_PROFILE, "Archive AV1")
    f = media_file(codec="hevc", processed_profile_id=7, tag="job=12;profile=7;v=1", age_days=400)
    d = decide(f, [rule(1, [cond("file_age_days", "gte", 365)], prof=stage2)], ctx_for(f))
    assert d.action == "transcode" and d.profile is stage2


def test_already_in_target_codec_is_skipped() -> None:
    p = profile(1, HEVC_PROFILE)
    f = media_file(codec="hevc")
    d = decide(f, [rule(1, prof=p)], ctx_for(f))
    assert d.action == "skip" and "nothing to gain" in d.reason


def test_same_codec_still_transcodes_when_profile_shrinks() -> None:
    p = profile(1, {**HEVC_PROFILE, "max_resolution": 1080})
    f = media_file(codec="hevc", short_side=2160)
    assert decide(f, [rule(1, prof=p)], ctx_for(f)).action == "transcode"
    p_fps = profile(2, {**HEVC_PROFILE, "max_fps": 30})
    f_fps = media_file(codec="hevc", fps=60)
    assert decide(f_fps, [rule(2, prof=p_fps)], ctx_for(f_fps)).action == "transcode"


def test_same_codec_guard_can_be_disabled() -> None:
    f = media_file(codec="hevc")
    assert decide(f, [rule(1, prof=profile(1, HEVC_PROFILE), skip_if_target_codec=False)], ctx_for(f)).action == "transcode"


def test_previously_failed_profile_is_not_retried_automatically() -> None:
    f = media_file()
    d = decide(f, [rule(1, prof=profile(3, HEVC_PROFILE))], ctx_for(f), failed_profile_ids={3})
    assert d.action == "skip" and "failed" in d.reason


def test_rule_without_profile_does_nothing() -> None:
    f = media_file()
    assert decide(f, [rule(1)], ctx_for(f)).action == "none"


def test_corrupt_stored_tree_is_skipped_not_crashing() -> None:
    f = media_file()
    bad = rule(1, prof=profile(1, HEVC_PROFILE))
    bad.conditions = {"type": "group", "op": "sometimes", "children": []}
    good = rule(2, prof=profile(2, AV1_PROFILE), position=1)
    assert decide(f, [bad, good], ctx_for(f)).profile.id == 2


def test_with_traces_reports_every_evaluated_rule() -> None:
    f = media_file(age_days=10)
    rules = [rule(1, [cond("file_age_days", "gt", 30)], prof=profile(1, HEVC_PROFILE)), rule(2, [cond("file_age_days", "gt", 5)], prof=profile(2, AV1_PROFILE), position=1)]
    d = decide(f, rules, ctx_for(f), with_traces=True)
    assert [(t["rule_id"], t["matched"]) for t in d.rule_traces] == [(1, False), (2, True)]


# ---------------------------------------------------------------------------
# Aging policy
# ---------------------------------------------------------------------------


def aging(stages: list[AgingStage], **kw: Any) -> list[Rule]:
    profiles = {7: profile(7, HEVC_PROFILE, "Archive H.265"), 8: profile(8, AV1_PROFILE, "Deep archive AV1")}
    body = AgingPolicyIn(stages=stages, **kw)
    rules = build_aging_rules(body, profiles, start_position=10)
    for r in rules:
        r.profile = profiles.get(r.profile_id) if r.profile_id else None
    for i, r in enumerate(rules):
        r.id = 100 + i
    return rules


POLICY = [
    AgingStage(min_days=0, action="keep"),
    AgingStage(min_days=30, action="transcode", profile_id=7),
    AgingStage(min_days=365, action="transcode", profile_id=8),
]


def test_aging_stages_become_contiguous_rules() -> None:
    rules = aging(POLICY)
    assert [r.name for r in rules] == ["Aging 0–30 days → keep original", "Aging 30–365 days → Archive H.265", "Aging 365+ days → Deep archive AV1"]
    assert [r.position for r in rules] == [10, 11, 12]
    assert [r.action for r in rules] == ["skip", "transcode", "transcode"]
    assert all(r.policy_group == "aging-all" for r in rules)


@pytest.mark.parametrize(
    ("age", "action", "profile_id"),
    [(0, "skip", None), (29.9, "skip", None), (30, "transcode", 7), (364, "transcode", 7), (365, "transcode", 8), (3000, "transcode", 8)],
)
def test_aging_boundaries(age: float, action: str, profile_id: int | None) -> None:
    f = media_file(age_days=age, codec="h264")
    d = decide(f, aging(POLICY), ctx_for(f))
    assert d.action == action
    assert (d.profile.id if d.profile else None) == profile_id


def test_aging_recorded_age_field() -> None:
    rules = aging(POLICY, age_field="recorded_age_days")
    f = media_file(age_days=1, codec="h264")  # copied to the NAS yesterday...
    c = ctx_for(f, creation_time=NOW - timedelta(days=400))  # ...but recorded over a year ago
    assert decide(f, rules, c).profile.id == 8


def test_aging_window_is_carried_on_rules() -> None:
    rules = aging(POLICY, window={"start": "01:00", "end": "07:00"})
    assert all(r.schedule == {"window": {"start": "01:00", "end": "07:00"}} for r in rules)


@pytest.mark.parametrize(
    "stages",
    [
        [AgingStage(min_days=30, action="keep"), AgingStage(min_days=0, action="keep")],
        [AgingStage(min_days=30, action="keep"), AgingStage(min_days=30, action="keep")],
        [AgingStage(min_days=30, action="transcode")],
    ],
)
def test_invalid_aging_policies(stages: list[AgingStage]) -> None:
    with pytest.raises(ValueError):
        AgingPolicyIn(stages=stages)


def test_kept_originals_are_left_alone_by_later_stages() -> None:
    # An original kept next to its output must not be converted again, e.g. by a second aging stage.
    f = media_file(processed_profile_id=1, role="kept_original")
    d = decide(f, [rule(1, prof=profile(2, AV1_PROFILE))], build_context(f))
    assert d.action == "none" and "Original kept" in d.reason
