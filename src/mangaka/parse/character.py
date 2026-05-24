"""Parser for the `05_character.md` layer output.

Format spec lives in `docs/SCHEMA.md` §4. The LLM emits one or more
character blocks shaped like:

    ## アリス (alice)
    ### 基本情報
    ...
    ### 外見 (Visual Identity)
    ...

This module returns a list of `(character_id, character_name, description)`
tuples; layer code wraps them into `Character` instances after generating
the sheet PNG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success

# Any line starting with `## ` is an H2 header that the LLM intends to be an
# entity block. If one of those lines doesn't carry a `(id)` capture, the
# round-1 parens fix would still silently absorb it into the previous block.
_H2_ANY_RE = re.compile(r"^##\s+(?P<text>[^\n]+)$", re.MULTILINE)

# Match `## 名前 (raw_id) <optional trailing notes>` tolerantly. We deliberately
# capture *any* parenthesized blob as `raw_id` and then validate it against
# `_VALID_ID_RE` below — otherwise an invalid ID (e.g. `## Bob (Bob)`) would
# fail the regex match outright and silently get appended to the previous
# block's description instead of producing a typed PARSE_ERROR.
_CHAR_HEADER_RE = re.compile(
    r"^##\s+(?P<name>[^\n(]+?)\s*\((?P<raw_id>[^)\n]+)\)[^\n]*$",
    re.MULTILINE,
)

# Reserved IDs that must NOT be used as Character IDs.
_RESERVED_CHARACTER_IDS = frozenset({"narrator", "self", "none", "null"})

# `[a-z][a-z0-9_]*`, length 2..32.
_VALID_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@dataclass(frozen=True)
class ParsedCharacter:
    """Intermediate parse result; layer code combines this with the sheet path."""

    id: str
    name: str
    description: str


def parse_character_markdown(md: str) -> Result[list[ParsedCharacter], MangaError]:
    """Split a Character layer Markdown blob into per-character blocks.

    Returns `Failure(PARSE_ERROR)` if no blocks are found or any ID is invalid /
    reserved / duplicated.
    """
    headers: list[tuple[str, str, int]] = []  # (id, name, start_offset)
    for m in _CHAR_HEADER_RE.finditer(md):
        headers.append((m.group("raw_id").strip(), m.group("name").strip(), m.start()))

    if not headers:
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message="no character blocks found (expected `## Name (id)` headers)",
            )
        )

    # Reject *any* `## ` line that doesn't carry a `(id)` capture — otherwise
    # the malformed header is silently merged into the previous block.
    matched_offsets = {start for _, _, start in headers}
    for any_m in _H2_ANY_RE.finditer(md):
        if any_m.start() not in matched_offsets:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=(
                        f"character header missing `(id)`: `## {any_m.group('text')}` — "
                        "every entity heading must be `## Name (id)`"
                    ),
                    detail={"line_start": any_m.start()},
                )
            )

    parsed: list[ParsedCharacter] = []
    seen_ids: set[str] = set()
    for i, (cid, name, start) in enumerate(headers):
        if not _VALID_ID_RE.match(cid):
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"invalid character_id: {cid!r}",
                    detail={"character_id": cid},
                )
            )
        if cid in _RESERVED_CHARACTER_IDS:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"reserved character_id used: {cid!r}",
                    detail={"character_id": cid},
                )
            )
        if cid in seen_ids:
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"duplicate character_id: {cid!r}",
                    detail={"character_id": cid},
                )
            )
        seen_ids.add(cid)

        end = headers[i + 1][2] if i + 1 < len(headers) else len(md)
        body = md[start:end].rstrip()
        parsed.append(ParsedCharacter(id=cid, name=name, description=body))

    return Success(parsed)


__all__ = ["ParsedCharacter", "parse_character_markdown"]
