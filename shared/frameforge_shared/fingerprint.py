"""Cheap, move-proof file fingerprints.

Hashing whole multi-GB recordings would be too slow for large libraries, so we hash
three 1 MiB samples (start, middle, end) plus the exact size. That's robust against renames and
moves, and changes whenever the content is rewritten.
"""

from __future__ import annotations

import os

import xxhash

SAMPLE_BYTES = 1024 * 1024


def fingerprint_file(path: str) -> str:
    size = os.path.getsize(path)
    h = xxhash.xxh3_128()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        if size <= SAMPLE_BYTES * 3:
            h.update(fh.read())
        else:
            for offset in (0, size // 2 - SAMPLE_BYTES // 2, size - SAMPLE_BYTES):
                fh.seek(offset)
                h.update(fh.read(SAMPLE_BYTES))
    return f"{size:x}-{h.hexdigest()}"
