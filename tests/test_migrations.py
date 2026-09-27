"""Data migrations (db/migrations/versions)."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from frameforge_server.db.session import MIGRATIONS_DIR


def _config(db: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    return cfg


def _validation(engine: sa.Engine) -> dict[str, dict]:
    with engine.connect() as conn:
        return {name: json.loads(v) for name, v in conn.execute(sa.text("SELECT name, validation FROM libraries"))}


def test_existing_libraries_switch_to_rejecting_larger_outputs(tmp_path: Path) -> None:
    db = tmp_path / "ff.db"
    cfg = _config(db)
    command.upgrade(cfg, "0001")
    engine = sa.create_engine(f"sqlite:///{db.as_posix()}")
    old = {"duration_tolerance_pct": 3.0, "min_output_bytes": 1048576, "max_size_ratio": 1.05, "fail_if_larger": False, "require_audio_if_source_has_audio": True}
    with engine.begin() as conn:
        for name, validation in (("VODs", old), ("Empty", {})):
            conn.execute(
                sa.text(
                    "INSERT INTO libraries (name, paths, enabled, automation_enabled, scan_interval_minutes, exclude_patterns,"
                    " output_policy, validation, created_at, last_scan_summary)"
                    " VALUES (:name, '[]', 1, 0, 60, '[]', 'backup', :v, '2026-09-18 00:00:00', '{}')"
                ),
                {"name": name, "v": json.dumps(validation)},
            )

    command.upgrade(cfg, "head")
    after = _validation(engine)
    assert after["VODs"] == {**old, "max_size_ratio": 1.0, "fail_if_larger": True}  # other thresholds untouched
    assert after["Empty"] == {"max_size_ratio": 1.0, "fail_if_larger": True}

    command.downgrade(cfg, "0001")
    assert _validation(engine)["VODs"] == old
    engine.dispose()


def test_storage_policy_split_backfills_from_output_policy(tmp_path: Path) -> None:
    db = tmp_path / "ff.db"
    cfg = _config(db)
    command.upgrade(cfg, "0002")
    engine = sa.create_engine(f"sqlite:///{db.as_posix()}")
    libs = {
        "Backup": ("backup", None),
        "Replace": ("replace", None),
        "Alongside": ("alongside", None),
        "Folder": ("output_dir", "/media/out"),
        "FolderWithoutPath": ("output_dir", None),
    }
    with engine.begin() as conn:
        for name, (policy, out) in libs.items():
            conn.execute(
                sa.text(
                    "INSERT INTO libraries (name, paths, enabled, automation_enabled, scan_interval_minutes, exclude_patterns,"
                    " output_policy, output_path, validation, created_at, last_scan_summary)"
                    " VALUES (:name, '[]', 1, 0, 60, '[]', :policy, :out, '{}', '2026-09-18 00:00:00', '{}')"
                ),
                {"name": name, "policy": policy, "out": out},
            )
        ids = dict(conn.execute(sa.text("SELECT name, id FROM libraries")).all())
        for i, (lib, fp) in enumerate([("Alongside", "fp-orig"), ("Folder", "fp-orig2"), ("Alongside", "fp-other")]):
            conn.execute(
                sa.text(
                    "INSERT INTO media_files (library_id, path, relative_path, filename, extension, size, mtime, fingerprint, status, ignored,"
                    " discovered_at, last_seen_at) VALUES (:lib, :path, 'a.mkv', 'a.mkv', '.mkv', 1, '2026-01-01 00:00:00', :fp, 'processed', 0,"
                    " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                ),
                {"lib": ids[lib], "path": f"/media/{i}.mkv", "fp": fp},
            )
        for fp in ("fp-orig", "fp-orig2"):
            conn.execute(sa.text("INSERT INTO processed_fingerprints (fingerprint, kind, created_at) VALUES (:fp, 'original', '2026-01-01 00:00:00')"), {"fp": fp})

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = {r[0]: tuple(r[1:]) for r in conn.execute(sa.text("SELECT name, output_location, original_handling, kept_original_location, retention_days, output_policy FROM libraries"))}
        roles = dict(conn.execute(sa.text("SELECT fingerprint, role FROM media_files")).all())
    assert rows["Backup"] == ("source_folder", "keep", "backup", None, "backup")
    assert rows["Replace"] == ("source_folder", "delete", "backup", None, "replace")
    assert rows["Alongside"] == ("source_folder", "keep", "in_place", None, "alongside")
    assert rows["Folder"] == ("folder", "keep", "backup", None, "output_dir")
    assert rows["FolderWithoutPath"] == ("source_folder", "keep", "backup", None, "backup")  # it always behaved like backup
    # Only originals kept next to their output are marked; separate-folder sources may get more outputs.
    assert roles == {"fp-orig": "kept_original", "fp-orig2": "source", "fp-other": "source"}

    command.downgrade(cfg, "0002")
    with engine.connect() as conn:
        policies = dict(conn.execute(sa.text("SELECT name, output_policy FROM libraries")).all())
        assert "retained_originals" not in sa.inspect(conn).get_table_names()
    assert policies["Replace"] == "replace" and policies["Folder"] == "output_dir"
    engine.dispose()
