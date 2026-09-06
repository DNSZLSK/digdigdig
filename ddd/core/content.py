"""Content checks shared by scan, rename and library imports."""

from collections import defaultdict
import hashlib
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duplicate_paths(files):
    """Group readable files by size then SHA-256; unreadable files stay unique."""
    sizes = defaultdict(list)
    for path in dict.fromkeys(Path(p) for p in files):
        try:
            sizes[path.stat().st_size].append(path)
        except OSError:
            continue
    groups = []
    for candidates in sizes.values():
        if len(candidates) < 2:
            continue
        hashes = defaultdict(list)
        for path in candidates:
            try:
                hashes[file_hash(path)].append(path)
            except OSError:
                continue
        groups.extend(group for group in hashes.values() if len(group) > 1)
    return groups
