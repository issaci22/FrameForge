"""Where converted files go and what happens to the original: a library's storage policy.

A library answers two separate questions:

* ``output_location``: next to the original (``source_folder``) or in a separate ``folder``.
* ``original_handling``: ``keep`` it, ``delete`` it once the output is verified, or keep it for
  ``retention_days`` and then delete it (``keep_days``, carried out by ``services/retention.py``).

When the original is kept next to the output, ``kept_original_location`` says whether it moves to a
backup folder (the output takes its name) or stays where it is (the output is named ``name.ff.ext``).

Older clients and older FrameForge versions only know the single ``output_policy`` value. It is still
accepted and always written back (derived, never deleting more than the new fields say), so a
downgrade keeps working. Migration 0003 carries its own copy of ``from_legacy``; a test keeps them equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..db.models import Library

OutputLocation = Literal["source_folder", "folder"]
OriginalHandling = Literal["keep", "delete", "keep_days"]
KeptOriginalLocation = Literal["backup", "in_place"]
LegacyPolicy = Literal["replace", "backup", "output_dir", "alongside"]

MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650
RETENTION_DAY_CHOICES = (1, 3, 7, 14, 30)


@dataclass(frozen=True)
class StoragePolicy:
    output_location: OutputLocation = "source_folder"
    original_handling: OriginalHandling = "keep"
    retention_days: int | None = None
    kept_original_location: KeptOriginalLocation = "backup"

    @property
    def deletes_immediately(self) -> bool:
        return self.original_handling == "delete"

    @property
    def timed(self) -> bool:
        return self.original_handling == "keep_days" and bool(self.retention_days)

    @property
    def legacy(self) -> LegacyPolicy:
        """The closest old value. It never deletes more than this policy (keep_days maps to a keeping policy)."""
        if self.output_location == "folder":
            return "output_dir"
        if self.original_handling == "delete":
            return "replace"
        if self.kept_original_location == "in_place":
            return "alongside"
        return "backup"


def from_legacy(policy: str | None, output_path: str | None) -> StoragePolicy:
    """Mirror what ``build_plan`` did with the old single value (including its fall-backs)."""
    if policy == "replace":
        return StoragePolicy("source_folder", "delete", None, "backup")
    if policy == "alongside":
        return StoragePolicy("source_folder", "keep", None, "in_place")
    if policy == "output_dir" and output_path:
        return StoragePolicy("folder", "keep", None, "backup")
    return StoragePolicy("source_folder", "keep", None, "backup")  # "backup", unknown values, output_dir without a folder


def storage_of(library: Library) -> StoragePolicy:
    """The effective policy of a library row. Rows built without the new columns fall back to the old value."""
    if not library.output_location:
        return from_legacy(library.output_policy, library.output_path)
    location: OutputLocation = "folder" if library.output_location == "folder" and library.output_path else "source_folder"
    handling = library.original_handling if library.original_handling in ("keep", "delete", "keep_days") else "keep"
    days = library.retention_days if handling == "keep_days" else None
    if handling == "keep_days" and not days:
        handling = "keep"  # never delete on a policy without a period
    kept = "in_place" if library.kept_original_location == "in_place" else "backup"
    if library.output_location == "folder" and not library.output_path:
        kept = "backup"  # same fall-back as the old output_dir without a folder
    return StoragePolicy(location, handling, days, kept)  # type: ignore[arg-type]


def apply_to(library: Library, policy: StoragePolicy) -> None:
    library.output_location = policy.output_location
    library.original_handling = policy.original_handling
    library.retention_days = policy.retention_days if policy.original_handling == "keep_days" else None
    library.kept_original_location = policy.kept_original_location
    library.output_policy = policy.legacy


def original_stays_in_library(policy: StoragePolicy) -> bool:
    """Whether a kept original remains a library file after conversion (so rules must not convert it again).

    An original kept forever next to outputs written to a separate folder is left alone on purpose: people
    use that layout to produce several outputs (say a proxy and an archive) from one source.
    """
    if policy.original_handling == "delete":
        return False
    if policy.output_location == "folder":
        return policy.original_handling == "keep_days"
    return policy.kept_original_location == "in_place"
