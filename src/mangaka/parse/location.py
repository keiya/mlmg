"""Parser for `06_location.md` layer output (same shape as Character)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success

# See parse/character.py for the H2-without-(id) rationale.
_H2_ANY_RE = re.compile(r"^##\s+(?P<text>[^\n]+)$", re.MULTILINE)

# See parse/character.py for the rationale on `raw_id` + post-validation.
_LOC_HEADER_RE = re.compile(
    r"^##\s+(?P<name>.+?)\s*[(（](?P<raw_id>[^)）\n]+)[)）][^(（\n]*$",
    re.MULTILINE,
)

_VALID_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# Reserved IDs per SCHEMA.md §2 — `self`, `none`, `null` are forbidden in
# *all* usages (not just characters). `narrator` is character-specific (it's
# a reserved Speech speaker_id, not a Character/Location id).
_RESERVED_LOCATION_IDS = frozenset({"self", "none", "null"})


@dataclass(frozen=True)
class ParsedLocation:
    id: str
    name: str
    description: str


def parse_location_markdown(md: str) -> Result[list[ParsedLocation], MangaError]:
    """Split a Location layer Markdown blob into per-location blocks."""
    headers: list[tuple[str, str, int]] = []
    for m in _LOC_HEADER_RE.finditer(md):
        # LLMs often wrap the id in markdown inline code (`shop`); strip the
        # surrounding backticks before validation so a cosmetic habit doesn't
        # fail an otherwise-valid id. Mirrors parse/character.py.
        raw_id = m.group("raw_id").strip().strip("`").strip()
        headers.append((raw_id, m.group("name").strip(), m.start()))

    if not headers:
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message="no location blocks found (expected `## Name (id)` headers)",
            )
        )

    matched_offsets = {start for _, _, start in headers}
    for any_m in _H2_ANY_RE.finditer(md):
        if any_m.start() not in matched_offsets:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=(
                        f"location header missing `(id)`: `## {any_m.group('text')}` — "
                        "every entity heading must be `## Name (id)`"
                    ),
                    detail={"line_start": any_m.start()},
                )
            )

    parsed: list[ParsedLocation] = []
    seen_ids: set[str] = set()
    for i, (lid, name, start) in enumerate(headers):
        if not _VALID_ID_RE.match(lid):
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"invalid location_id: {lid!r}",
                    detail={"location_id": lid},
                )
            )
        if lid in _RESERVED_LOCATION_IDS:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"reserved location_id used: {lid!r}",
                    detail={"location_id": lid},
                )
            )
        if lid in seen_ids:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"duplicate location_id: {lid!r}",
                    detail={"location_id": lid},
                )
            )
        seen_ids.add(lid)

        end = headers[i + 1][2] if i + 1 < len(headers) else len(md)
        body = md[start:end].rstrip()
        parsed.append(ParsedLocation(id=lid, name=name, description=body))

    return Success(parsed)


__all__ = ["ParsedLocation", "parse_location_markdown"]
