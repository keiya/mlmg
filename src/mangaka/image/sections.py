"""Stylist Markdown section extractor.

`Stylist.raw_markdown` is a 10-section guide; downstream prompts pull
section subsets via `SECTION_SETS` (see `docs/SCHEMA.md` §3).

`extract_sections(md, [4, 5, 6, 10])` returns the concatenation of the
`## 4. ...`, `## 5. ...`, `## 6. ...`, `## 10. ...` blocks. Section numbers
that are missing from the source are silently skipped — the layer code may
choose to fail or warn.
"""

from __future__ import annotations

import re

# Per docs/SCHEMA.md §3: which sections each downstream prompt uses.
SECTION_SETS: dict[str, list[int]] = {
    "style_ref":       [4, 5, 6, 10],
    "character_sheet": [4, 5, 6, 7, 10],
    "location_sheet":  [4, 5, 6, 8, 10],
    "page_beat":       [1, 2, 3, 9, 10],
    "page_render":     [4, 5, 6, 9, 10],
}

# Tolerant section header pattern: `## N` or `## N.` or `## N something`.
# Matches at start of line, optionally allowing whitespace + leading bold.
_HEADER_RE = re.compile(
    r"^##\s*\*{0,2}\s*(?P<n>\d{1,2})[.\s]",
    re.MULTILINE,
)


def _index_sections(md: str) -> dict[int, tuple[int, int]]:
    """Return `{section_number: (header_start, content_end)}`.

    `content_end` is the start of the next H2 header (or end of document).
    """
    headers: list[tuple[int, int]] = []  # (section_no, start_offset)
    for m in _HEADER_RE.finditer(md):
        headers.append((int(m.group("n")), m.start()))

    spans: dict[int, tuple[int, int]] = {}
    for i, (num, start) in enumerate(headers):
        end = headers[i + 1][1] if i + 1 < len(headers) else len(md)
        # Prefer the *first* occurrence of each number — if the LLM emits two
        # `## 4`, the second one is likely a sub-mention, not a fresh section.
        spans.setdefault(num, (start, end))
    return spans


def extract_sections(stylist_md: str, section_nos: list[int]) -> str:
    """Return the requested sections of `stylist_md` joined by blank lines.

    Sections appear in `section_nos` order. Missing sections are skipped —
    callers that need a strict "all-or-nothing" guarantee should use
    `missing_sections()` first.
    Trailing whitespace inside each section is trimmed.
    """
    spans = _index_sections(stylist_md)
    parts: list[str] = []
    for num in section_nos:
        span = spans.get(num)
        if span is None:
            continue
        start, end = span
        parts.append(stylist_md[start:end].rstrip())
    return "\n\n".join(parts)


def missing_sections(stylist_md: str, section_nos: list[int]) -> list[int]:
    """Return the subset of `section_nos` whose `## N` header is absent."""
    spans = _index_sections(stylist_md)
    return [n for n in section_nos if n not in spans]


__all__ = ["SECTION_SETS", "extract_sections", "missing_sections"]
