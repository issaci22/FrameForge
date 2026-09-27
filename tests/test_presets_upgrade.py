"""Built-in lineup: fresh seeding, and the one-time upgrade of the first lineup (services/presets.py)."""

from __future__ import annotations

from sqlalchemy import select

from frameforge_server.db.models import Profile, SystemSetting
from frameforge_server.db.session import sessionmaker
from frameforge_server.services import presets
from frameforge_shared.compat import check_spec, has_errors
from frameforge_shared.profile import ProfileSpec


def v1_spec(key: str) -> dict:
    return ProfileSpec.model_validate(presets.LEGACY_SEEDS_V1[key][1]).model_dump(mode="json")


async def install_v1(*, edit: dict | None = None, rename: str | None = None, delete: str | None = None, extra: list[str] | None = None) -> None:
    """A database as the first release left it: all seven built-ins seeded, optionally changed by the user."""
    seeded = ["youtube_archive", "youtube_upload", "long_term_av1", "storage_saver", "stream_vod", "obs_remux", "editing_proxy"]
    async with sessionmaker()() as db:
        for key in seeded:
            if key == delete:
                continue
            if key in presets.LEGACY_SEEDS_V1:
                name, _ = presets.LEGACY_SEEDS_V1[key]
                spec = v1_spec(key)
            else:
                b = presets.BUILTIN_BY_KEY[key]
                name, spec = b.name, presets.spec_of(b)
            if key == "youtube_archive" and edit:
                spec = {**spec, **edit}
            if key == "youtube_archive" and rename:
                name = rename
            db.add(Profile(name=name, description="", builtin_key=key, spec=spec))
        for name in extra or []:
            db.add(Profile(name=name, description="mine", spec=ProfileSpec().model_dump(mode="json")))
        db.add(SystemSetting(key="seeded_profiles", value=seeded))
        await db.commit()


async def profiles() -> dict[str, Profile]:
    async with sessionmaker()() as db:
        return {p.name: p for p in (await db.execute(select(Profile))).scalars()}


async def seed() -> None:
    async with sessionmaker()() as db:
        await presets.seed_profiles(db)


async def test_fresh_install_gets_the_whole_lineup(db_settings) -> None:  # noqa: ANN001
    await seed()
    got = await profiles()
    assert set(got) == {b.name for b in presets.BUILTINS}
    assert got["Balanced"].spec["container"] == "mp4" and got["Balanced"].spec["video_codec"] == "hevc"
    assert got["High Quality"].spec["container"] == "mkv"


def test_goal_profiles_are_valid_and_ordered_by_intent() -> None:
    for b in presets.BUILTINS:
        assert not has_errors(check_spec(ProfileSpec.model_validate(b.spec))), b.key
    goal = [b for b in presets.BUILTINS if b.group == "goal"]
    assert [b.key for b in goal] == ["max_compat", "balanced", "space_saver", "smallest_av1", "high_quality"]
    q = {b.key: b.spec.get("quality") for b in goal}
    assert q["space_saver"] < q["balanced"] < q["high_quality"]


async def test_unedited_first_lineup_is_upgraded_in_place(db_settings) -> None:  # noqa: ANN001
    await install_v1()
    before = await profiles()
    ids = {k: before[presets.LEGACY_SEEDS_V1[k][0]].id for k in presets.LEGACY_SEEDS_V1}
    await seed()
    after = await profiles()
    assert "YouTube Archive" not in after and "Storage Saver" not in after
    assert after["Balanced"].id == ids["youtube_archive"], "rules keep pointing at the same profile"
    assert after["Balanced"].builtin_key == "balanced"
    assert after["Space Saver"].id == ids["storage_saver"]
    assert after["Smallest Files (AV1)"].id == ids["long_term_av1"]
    assert "Maximum Compatibility" in after and "High Quality" in after
    async with sessionmaker()() as db:
        assert len(await presets.notices(db)) == 3


async def test_edited_built_in_is_kept_and_the_new_one_added(db_settings) -> None:  # noqa: ANN001
    await install_v1(edit={"quality": 80})
    await seed()
    got = await profiles()
    assert got["YouTube Archive"].spec["quality"] == 80
    assert got["YouTube Archive"].builtin_key == "youtube_archive"
    assert "Balanced" in got


async def test_renamed_built_in_counts_as_edited(db_settings) -> None:  # noqa: ANN001
    await install_v1(rename="My Archive")
    await seed()
    got = await profiles()
    assert got["My Archive"].spec == v1_spec("youtube_archive")
    assert "Balanced" in got


async def test_deleted_built_in_stays_deleted(db_settings) -> None:  # noqa: ANN001
    await install_v1(delete="youtube_archive")
    await seed()
    got = await profiles()
    assert "Balanced" not in got and "YouTube Archive" not in got


async def test_name_collision_skips_the_upgrade(db_settings) -> None:  # noqa: ANN001
    await install_v1(extra=["Balanced"])
    await seed()
    got = await profiles()
    assert got["YouTube Archive"].spec == v1_spec("youtube_archive")
    assert got["Balanced"].description == "mine"


async def test_upgrade_runs_only_once(db_settings) -> None:  # noqa: ANN001
    await install_v1()
    await seed()
    # The user later sets a profile back to the old settings under the old name: it must stay that way.
    async with sessionmaker()() as db:
        p = (await db.execute(select(Profile).where(Profile.name == "Space Saver"))).scalar_one()
        p.name, p.spec, p.builtin_key = "Storage Saver", v1_spec("storage_saver"), "storage_saver"
        await db.commit()
    await seed()
    got = await profiles()
    assert got["Storage Saver"].spec == v1_spec("storage_saver")
