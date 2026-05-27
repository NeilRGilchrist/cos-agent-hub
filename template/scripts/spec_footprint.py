"""
spec_footprint.py — Pure-function helpers for FR `owns:`/`reads:` footprint metadata.

Surfaces three things to the indexer (`scripts/index-specs.py`):

1. `parse_footprint(frontmatter)` — validate and lift the `owns:`/`reads:` lists
   off a parsed YAML frontmatter dict, returning a frozen dataclass.
2. `expand_globs(globs, repo_files)` — expand a list of POSIX-style globs
   against an iterable of repo-relative file paths (typically `git ls-files`
   output) into a frozen set of matched files.
3. `find_overlaps(frs)` — given a list of FR records carrying their parsed
   footprints, return the pairwise `owns:` overlaps among FRs in active
   states (`ready`, `in-progress`, `in-review`). Merged/deployed FRs cannot
   collide with anything by definition; draft FRs are not yet candidates
   for dispatch.

This module is deliberately pure: no I/O, no logging, no side effects. The
indexer is responsible for shelling out to `git ls-files` and feeding the
result in. That keeps unit tests trivial and lets the CODEOWNERS emitter
re-use `expand_globs` against the same cached file list.

Globbing semantics (intentionally minimal):
  - `**` matches any number of path segments, including zero. So
    `src/foo/**` matches both `src/foo/bar.py` and `src/foo/sub/baz.py`,
    and (matching zero segments) `src/foo` itself if it appears in the
    file list as a tree entry — though `git ls-files` only emits files,
    so the zero-segment case is generally inert.
  - `*` matches any number of characters EXCEPT `/`. So `src/*/x.py`
    matches `src/a/x.py` but not `src/a/b/x.py`.
  - `?` matches exactly one character except `/`.
  - No regex, no negation (`!`), no brace expansion (`{a,b}`).

Python 3.13's `Path.full_match` would be the natural fit but is too new
for the 3.11 floor; we hand-translate to a small regex instead. Using
`fnmatch.fnmatch` with `**` → `*` collapse would conflate the
single-segment and multi-segment cases (Python's `fnmatch` does not stop
at path separators), which is wrong for `*`. The hand-translator below is
the smallest thing that gets both `*` and `**` correct on POSIX paths.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


class FootprintInvalid(ValueError):
    """Raised when an FR's `owns:` or `reads:` frontmatter is malformed."""


@dataclass(frozen=True)
class Footprint:
    owns: tuple[str, ...]
    reads: tuple[str, ...]


@dataclass(frozen=True)
class FRRecord:
    """Minimal view of an FR consumed by `find_overlaps`."""

    id: str
    status: str
    footprint: Footprint


@dataclass(frozen=True)
class Overlap:
    fr_a: str
    fr_b: str
    shared_files: frozenset[str]


_ACTIVE_STATUSES = frozenset({"ready", "in-progress", "in-review"})


def _validate_glob(fr_id: str, field_name: str, value: object) -> str:
    """Validate a single glob entry. Returns the glob string on success, raises on failure."""
    if not isinstance(value, str):
        raise FootprintInvalid(
            f"{fr_id}: {field_name} entry must be a string, got {type(value).__name__}: {value!r}"
        )
    if not value:
        raise FootprintInvalid(f"{fr_id}: {field_name} entry must not be empty")
    if value.startswith("/"):
        raise FootprintInvalid(
            f"{fr_id}: {field_name} entry {value!r} is absolute; globs must be repo-relative POSIX paths"
        )
    # Reject `..` as a path segment, regardless of position. We split on '/'
    # rather than substring-checking so that legitimate filenames containing
    # ".." (rare but legal) wouldn't trip the guard.
    segments = value.split("/")
    if any(seg == ".." for seg in segments):
        raise FootprintInvalid(
            f"{fr_id}: {field_name} entry {value!r} contains '..'; "
            "parent traversal is not allowed"
        )
    if "\\" in value:
        raise FootprintInvalid(
            f"{fr_id}: {field_name} entry {value!r} contains backslash; "
            "use POSIX forward slashes"
        )
    return value


def parse_footprint(frontmatter: dict) -> Footprint:
    """
    Lift `owns:` and `reads:` off a parsed YAML frontmatter dict.

    Missing fields are treated as `[]` (empty tuple). Present fields MUST
    be lists of strings; any other shape raises `FootprintInvalid` with a
    message naming the FR ID and the offending value.

    The FR ID is read from `frontmatter["id"]` if present, falling back to
    `"<unknown>"` for the error message — the caller is the indexer and
    has already validated `id`, so the fallback is defensive only.
    """
    fr_id = str(frontmatter.get("id") or "<unknown>")

    def _lift(field_name: str) -> tuple[str, ...]:
        raw = frontmatter.get(field_name)
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise FootprintInvalid(
                f"{fr_id}: {field_name} must be a YAML list of glob strings, "
                f"got {type(raw).__name__}: {raw!r}"
            )
        return tuple(_validate_glob(fr_id, field_name, item) for item in raw)

    return Footprint(owns=_lift("owns"), reads=_lift("reads"))


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """
    Translate a POSIX-style glob to a compiled anchored regex.

    Recognised tokens:
      - `**`  → `.*`         (matches across path separators)
      - `*`   → `[^/]*`      (matches within a single segment)
      - `?`   → `[^/]`       (matches one non-separator character)
      - everything else is regex-escaped

    Trailing `/**` is treated as "this directory and everything below it":
    `src/foo/**` matches `src/foo/bar.py` AND, by collapsing the trailing
    `/`, `src/foo` itself if such a tree-entry ever appeared. Leading
    `**/` similarly matches the zero-segment case.
    """
    parts: list[str] = []
    i = 0
    n = len(glob)
    while i < n:
        ch = glob[i]
        if ch == "*":
            if i + 1 < n and glob[i + 1] == "*":
                parts.append(".*")
                i += 2
                # If the `**` is followed by `/`, swallow the slash so that
                # `src/foo/**` (which we treat as a prefix) matches
                # `src/foo` as well as `src/foo/bar.py`. Without this, the
                # regex would require at least one path segment after
                # `src/foo/`.
                if i < n and glob[i] == "/":
                    parts.append("/?")
                    i += 1
            else:
                parts.append("[^/]*")
                i += 1
        elif ch == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


@lru_cache(maxsize=512)
def _compile(glob: str) -> re.Pattern[str]:
    return _glob_to_regex(glob)


def expand_globs(globs: Iterable[str], repo_files: Iterable[str]) -> frozenset[str]:
    """
    Return the frozen set of `repo_files` matching ANY of `globs`.

    Both inputs are iterables of POSIX-style relative paths (forward
    slashes, repo-root-relative — the same shape `git ls-files` emits on
    every platform, including Windows). Pre-compiled regexes are cached
    per-glob so repeated calls (e.g. once per FR by the CODEOWNERS
    emitter) don't re-translate the same pattern.
    """
    patterns = [_compile(g) for g in globs]
    if not patterns:
        return frozenset()
    files = list(repo_files)
    matched: set[str] = set()
    for f in files:
        # POSIX-normalise just in case the caller passed Windows paths.
        norm = f.replace("\\", "/")
        for p in patterns:
            if p.match(norm):
                matched.add(norm)
                break
    return frozenset(matched)


def find_overlaps(
    frs: list[FRRecord],
    repo_files: Iterable[str] | None = None,
) -> list[Overlap]:
    """
    Return pairwise `owns:` overlaps among FRs in active states.

    "Active" = status in {ready, in-progress, in-review}. Merged FRs are
    ignored (their work is done; their files cannot conflict with new
    work). Draft FRs are ignored (they are not yet dispatch candidates).

    If `repo_files` is None, the function shells out to `git ls-files`
    once. Pass an explicit iterable in tests to keep them hermetic.

    Output is sorted by (fr_a, fr_b) for deterministic INDEX.md
    rendering.
    """
    if repo_files is None:
        repo_files = _git_ls_files()
    files = list(repo_files)

    active = [fr for fr in frs if fr.status in _ACTIVE_STATUSES]
    expanded: dict[str, frozenset[str]] = {
        fr.id: expand_globs(fr.footprint.owns, files) for fr in active
    }

    overlaps: list[Overlap] = []
    by_id = sorted(active, key=lambda f: f.id)
    for i, a in enumerate(by_id):
        for b in by_id[i + 1 :]:
            shared = expanded[a.id] & expanded[b.id]
            if shared:
                overlaps.append(Overlap(fr_a=a.id, fr_b=b.id, shared_files=shared))
    overlaps.sort(key=lambda o: (o.fr_a, o.fr_b))
    return overlaps


def _git_ls_files() -> list[str]:
    """Run `git ls-files` from the repo root and return POSIX-normalised paths."""
    repo_root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # The indexer can run outside a git checkout (rare; a tarball
        # extract for archival, say). Returning an empty list lets the
        # rest of the indexer continue; overlap detection will be a no-op
        # which is the correct degenerate behaviour.
        print(
            f"WARN: spec_footprint: git ls-files failed ({e}); overlap detection skipped",
            file=sys.stderr,
        )
        return []
    return [line.replace("\\", "/") for line in result.stdout.splitlines() if line]
