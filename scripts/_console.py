#!/usr/bin/env python3
"""_console.py -- Encoding-safe console output shared by the hub scripts.

Windows consoles default to cp1252, or to an OEM codepage such as cp437 or
cp850. None of them can represent the full set of typographic characters that
show up in FR titles, idea titles, and progress messages: cp1252 has no
rightwards arrow, and the OEM pages have no em dash either. Writing one through
the builtin `print()` raises UnicodeEncodeError and aborts the script mid-run.
In `bootstrap.py` that abort leaves a half-created project directory behind,
which bootstrap then refuses to reuse on the next attempt.

Two layers, applied in order:

1. `configure_stdio()` switches stdout/stderr to UTF-8 so a modern terminal
   renders the real characters with full fidelity.
2. `safe_print()` catches the UnicodeEncodeError that survives -- a stream that
   cannot be reconfigured -- and folds the text to documented ASCII
   equivalents instead of dying.

Fixing this at the stream rather than per-string is deliberate. Commit a75233c
hand-replaced the offending characters in `bootstrap.py` with ASCII, and they
crept straight back in when the compliance-profile support landed. Per-string
substitution is not a durable fix; the next contributor who types an arrow
re-breaks it.

This mirrors the two-layer pattern already proven in
`template/scripts/agent-status.py`. That copy stays standalone on purpose:
template scripts are copied into bootstrapped projects, which have no access to
this hub-level module.

Unlike that copy, the fold table below is written with numeric escape sequences
rather than literal characters: a module whose entire job is surviving bad
encodings should not itself depend on being decoded correctly.
"""

from __future__ import annotations

import sys
import unicodedata
from typing import TextIO

# Documented ASCII equivalents for the typographic characters that appear in
# hub and project metadata. Anything not listed here is folded via Unicode
# decomposition; nothing becomes '?' or U+FFFD.
_ASCII_FOLD = {
    "\u2014": "-",    # em dash
    "\u2013": "-",    # en dash
    "\u2012": "-",    # figure dash
    "\u2015": "-",    # horizontal bar
    "\u2192": "->",   # rightwards arrow
    "\u2190": "<-",   # leftwards arrow
    "\u2194": "<->",  # left-right arrow
    "\u2018": "'",    # left single quote
    "\u2019": "'",    # right single quote
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2026": "...",  # ellipsis
    "\u2705": "OK",   # white heavy check mark (bootstrap completion banner)
}


def _ascii_fold(text: str) -> str:
    """Fold `text` to ASCII deterministically, without replacement characters.

    Known typographic characters map to their documented equivalents; any
    residual non-ASCII is decomposed (NFKD) and stripped of combining marks so
    accented letters keep their base form. The result always encodes as ASCII.
    """
    for uni, repl in _ASCII_FOLD.items():
        text = text.replace(uni, repl)
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def safe_print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    file: TextIO | None = None,
) -> None:
    """Print like the builtin, but fall back to ASCII folding when the target
    stream's encoding cannot represent a character (e.g. cp1252 + arrow).

    On a UTF-8-capable stream the write path is unchanged, so output stays
    byte-identical to the builtin `print`.
    """
    stream = sys.stdout if file is None else file
    text = sep.join(str(a) for a in args) + end
    try:
        stream.write(text)
    except UnicodeEncodeError:
        stream.write(_ascii_fold(text))


def configure_stdio() -> None:
    """Best-effort: reconfigure stdout/stderr to UTF-8 so text prints with full
    fidelity on any modern terminal. Where reconfiguration is unavailable, the
    `safe_print` folding path keeps output crash-free and legible.

    Call this once, as early as possible, before anything is written.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
