"""PageBeat parser (Markdown + YAML frontmatter).

Two-phase per SCHEMA §7:
- **Phase 1 (tolerant)**: split frontmatter from body, split panels by `## Panel N`,
  best-effort extract Visual / Camera / Emotion / Speech / SFX. Preserve partial
  data (missing fields become `None`).
- **Phase 2 (strict)**: validate all required fields + enum values + ID references.
  Any miss → `Failure(PARSE_ERROR)`. Caller (layer code) retries with feedback.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import frontmatter  # type: ignore[import-untyped]
import yaml

from mangaka.domain import SFX, SpeechIntent
from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success

SIZE_HINT_VALUES: frozenset[str] = frozenset({"regular", "large", "wide"})
BUBBLE_TYPE_VALUES: frozenset[str] = frozenset(
    {"dialogue", "inner_monologue", "narration", "shout"}
)
SPEAKER_RESERVED_IDS: frozenset[str] = frozenset({"narrator"})


# ---------------------------------------------------------------------------
# Phase 1: tolerant parse — returns ParsedPageBeat with possible Nones.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedFrontmatter:
    """Raw frontmatter values; Nones survive Phase 1 for Phase 2 to surface."""

    page_number: int | None
    phase: str | None
    location_id: str | None
    character_ids: list[str] | None
    mood: str | None
    continuity_note: str | None


@dataclass(frozen=True)
class ParsedPanel:
    """Panel as captured by Phase 1; required fields may be None."""

    panel_no: int
    size_hint: str
    visual: str | None
    emotion: str | None
    camera: str | None
    speech_intents: list[SpeechIntent]
    sfx: list[SFX]


@dataclass(frozen=True)
class ParsedPageBeat:
    """Phase 1 output. Pass to `validate_page_beat` for Phase 2 checks."""

    frontmatter: ParsedFrontmatter
    panels: list[ParsedPanel]


# Tolerant ```markdown / ```yaml / ``` fence wrapper stripper. LLMs frequently
# echo the prompt's example fence; without this the body's frontmatter is
# hidden behind the fence and `frontmatter.loads` sees no YAML head, burning
# the entire parse retry budget on otherwise-valid output.
_FENCE_RE = re.compile(
    r"^```(?:[A-Za-z0-9_+\-]*)\s*\n(?P<body>.*?)\n```\s*$",
    re.DOTALL | re.MULTILINE,
)


# `## Panel N [size: X]` — tolerant: whitespace, missing brackets fall back to regular.
_PANEL_HEADER_RE = re.compile(
    r"^##\s+Panel\s+(?P<no>\d+)\s*(?:\[size:\s*(?P<size>\w+)\s*\])?\s*$",
    re.MULTILINE,
)


def _strip_code_fence(text: str) -> str:
    """Strip a single surrounding ``` ... ``` fence if present; else return as-is."""
    stripped = text.strip()
    m = _FENCE_RE.search(stripped)
    if m:
        return m.group("body").strip()
    return stripped

# `**Field**: value` or `**Field**:\n value` (multi-line until next `**Field**:` / next H2 / EOF).
_FIELD_RE = re.compile(
    r"^\*\*(?P<name>Visual|Camera|Emotion|Speech|SFX)\*\*\s*:\s*(?P<value>.*?)"
    r"(?=^\*\*(?:Visual|Camera|Emotion|Speech|SFX)\*\*\s*:|^##|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Speech: `- [speaker / bubble_type / register] text`
_SPEECH_RE = re.compile(
    r"^-\s*\[\s*(?P<speaker>[^/\]]+?)\s*/\s*(?P<bubble>[^/\]]+?)\s*/\s*"
    r"(?P<register>[^/\]]*?)\s*\]\s*(?P<text>.+?)\s*$",
    re.MULTILINE,
)

# SFX: `- text (role)`
_SFX_RE = re.compile(r"^-\s*(?P<text>[^(\n]+?)\s*\((?P<role>[^)]+)\)\s*$", re.MULTILINE)


def _parse_frontmatter_values(meta: dict[str, Any]) -> ParsedFrontmatter:
    """Extract typed values from the YAML frontmatter dict; tolerate missing."""
    page_number = meta.get("page_number")
    if not isinstance(page_number, int):
        page_number = None

    char_ids_raw = meta.get("character_ids")
    character_ids: list[str] | None
    if isinstance(char_ids_raw, list) and all(  # type: ignore[redundant-expr]
        isinstance(x, str) for x in cast("list[Any]", char_ids_raw)
    ):
        character_ids = [cast("str", x) for x in cast("list[Any]", char_ids_raw)]
    else:
        character_ids = None

    return ParsedFrontmatter(
        page_number=page_number,
        phase=meta.get("phase") if isinstance(meta.get("phase"), str) else None,
        location_id=(
            meta.get("location_id") if isinstance(meta.get("location_id"), str) else None
        ),
        character_ids=character_ids,
        mood=meta.get("mood") if isinstance(meta.get("mood"), str) else None,
        continuity_note=(
            meta.get("continuity_note")
            if isinstance(meta.get("continuity_note"), str)
            else None
        ),
    )


def _parse_speech_block(body: str) -> list[SpeechIntent]:
    """Parse list of Speech lines. Malformed lines are skipped silently per §7
    "Speech/SFX 行のフォーマット違反は warning、その行だけスキップ".
    """
    if not body.strip() or body.strip() in ("なし", "none", "None", "null"):
        return []
    speeches: list[SpeechIntent] = []
    for m in _SPEECH_RE.finditer(body):
        register_raw = m.group("register").strip()
        # Tolerate `"..."` or `「...」` wrapping the dialogue.
        raw_text = m.group("text").strip()
        if len(raw_text) >= 2 and (
            raw_text[0] == raw_text[-1] == '"'
            or (raw_text[0] == "「" and raw_text[-1] == "」")
        ):
            raw_text = raw_text[1:-1]
        speeches.append(
            SpeechIntent(
                speaker_id=m.group("speaker").strip(),
                bubble_type=m.group("bubble").strip(),
                text=raw_text,
                register=register_raw or None,
            )
        )
    return speeches


def _parse_sfx_block(body: str) -> list[SFX]:
    if not body.strip() or body.strip() in ("なし", "none", "None", "null"):
        return []
    out: list[SFX] = []
    for m in _SFX_RE.finditer(body):
        out.append(SFX(text=m.group("text").strip(), role=m.group("role").strip()))
    return out


def _parse_panel(panel_no: int, size_hint: str, body: str) -> ParsedPanel:
    """Extract Visual / Camera / Emotion / Speech / SFX from one Panel body.

    `size_hint` is kept raw — Phase 2 rejects unknown values so the LLM can
    self-correct via parse-retry. The Phase-1 default for an *absent* size
    bracket is "regular" (handled by the panel-header regex), not for an
    unrecognized one.
    """
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(body):
        fields[m.group("name")] = m.group("value").strip()

    return ParsedPanel(
        panel_no=panel_no,
        size_hint=size_hint,
        visual=fields.get("Visual") or None,
        emotion=fields.get("Emotion") or None,
        camera=fields.get("Camera") or None,
        speech_intents=_parse_speech_block(fields.get("Speech", "")),
        sfx=_parse_sfx_block(fields.get("SFX", "")),
    )


def parse_page_beat_text(text: str) -> Result[ParsedPageBeat, MangaError]:
    """Phase 1 parse. Frontmatter errors are fatal; panel-body errors are tolerated.

    The LLM commonly wraps its response in a ```markdown fence (the prompt
    example uses one); we strip that before handing the body to `frontmatter`.
    """
    text = _strip_code_fence(text)
    try:
        post = frontmatter.loads(text)  # type: ignore[no-untyped-call]
    except yaml.YAMLError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.FRONTMATTER_INVALID,
                message=f"PageBeat frontmatter YAML parse failed: {exc}",
            )
        )
    except Exception as exc:  # python-frontmatter wraps some errors
        return Failure(
            MangaError(
                kind=ErrorKind.FRONTMATTER_INVALID,
                message=f"PageBeat frontmatter parse failed: {exc}",
            )
        )

    meta = cast("dict[str, Any]", post.metadata)  # type: ignore[no-untyped-call]
    fm = _parse_frontmatter_values(meta)
    body = cast("str", post.content)  # type: ignore[no-untyped-call]

    # Split panels by header offsets.
    headers: list[tuple[int, str, int]] = []  # (panel_no, size, start_offset)
    for m in _PANEL_HEADER_RE.finditer(body):
        headers.append(
            (int(m.group("no")), (m.group("size") or "regular").strip(), m.start())
        )

    panels: list[ParsedPanel] = []
    for i, (pno, size, start) in enumerate(headers):
        end = headers[i + 1][2] if i + 1 < len(headers) else len(body)
        panel_body = body[start:end]
        panels.append(_parse_panel(pno, size, panel_body))

    return Success(ParsedPageBeat(frontmatter=fm, panels=panels))


# ---------------------------------------------------------------------------
# Phase 2: strict validation
# ---------------------------------------------------------------------------


def _missing(field: str) -> Failure[MangaError]:
    return Failure(
        MangaError(
            kind=ErrorKind.VALIDATION_FAILED,
            message=f"PageBeat missing required field: {field}",
            detail={"missing": field},
        )
    )


def _validate_frontmatter(
    fm: ParsedFrontmatter,
    *,
    expected_page_number: int | None,
    known_character_ids: set[str],
    known_location_ids: set[str],
    known_arc_phases: set[str] | None,
) -> Result[None, MangaError]:
    if fm.page_number is None:
        return _missing("page_number")
    if fm.phase is None:
        return _missing("phase")
    if fm.location_id is None:
        return _missing("location_id")
    if fm.character_ids is None:
        return _missing("character_ids")
    if fm.mood is None:
        return _missing("mood")

    # An empty list is structurally present but semantically wrong: a page
    # with no characters has no faces to put in the ref budget, and the
    # downstream PageRender prompt's 【登場人物】 block would be empty.
    if not fm.character_ids:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message="PageBeat character_ids must list at least one character",
            )
        )

    if expected_page_number is not None and fm.page_number != expected_page_number:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=(
                    f"PageBeat page_number={fm.page_number} != "
                    f"expected {expected_page_number}"
                ),
            )
        )
    if fm.location_id not in known_location_ids:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=f"PageBeat references unknown location_id={fm.location_id!r}",
                detail={"location_id": fm.location_id},
            )
        )
    for cid in fm.character_ids:
        if cid not in known_character_ids:
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=f"PageBeat references unknown character_id={cid!r}",
                    detail={"character_id": cid},
                )
            )
    # `phase` must match one of PagePlan's arc.phase values when the caller
    # supplied the arc-phase set. Otherwise the LLM could hallucinate a
    # phase label that propagates into Page.beat.phase and confuses any
    # later logic gated on the phase being known.
    if known_arc_phases is not None and fm.phase not in known_arc_phases:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=(
                    f"PageBeat phase={fm.phase!r} not in PagePlan arc phases "
                    f"{sorted(known_arc_phases)}"
                ),
                detail={"phase": fm.phase},
            )
        )
    return Success(None)


def _validate_panels(
    panels: list[ParsedPanel],
    *,
    page_character_ids: set[str],
    max_panels_per_page: int,
) -> Result[None, MangaError]:
    if not panels:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message="PageBeat has no panels",
            )
        )
    if len(panels) > max_panels_per_page:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=(
                    f"PageBeat has {len(panels)} panels, "
                    f"exceeds max_panels_per_page={max_panels_per_page}"
                ),
            )
        )

    # 1-indexed, contiguous panel numbering.
    for i, panel in enumerate(panels):
        if panel.panel_no != i + 1:
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=(
                        f"Panel ordering broken: position {i} has panel_no="
                        f"{panel.panel_no}, expected {i + 1}"
                    ),
                )
            )

    # Speakers must be members of THIS page's character_ids (plus narrator),
    # NOT the global Character roster. PageRender / Ref Builder only pull
    # sheets for frontmatter.character_ids — a speaker outside that list
    # would draw a character with no ref image attached.
    speaker_allowed = page_character_ids | SPEAKER_RESERVED_IDS
    for panel in panels:
        if panel.size_hint not in SIZE_HINT_VALUES:
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=(
                        f"Panel {panel.panel_no} has invalid size_hint="
                        f"{panel.size_hint!r} (expected one of "
                        f"{sorted(SIZE_HINT_VALUES)})"
                    ),
                    detail={"panel_no": panel.panel_no, "size_hint": panel.size_hint},
                )
            )
        if not panel.visual:
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=f"Panel {panel.panel_no} missing Visual",
                    detail={"panel_no": panel.panel_no},
                )
            )
        if not panel.emotion:
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=f"Panel {panel.panel_no} missing Emotion",
                    detail={"panel_no": panel.panel_no},
                )
            )
        for si in panel.speech_intents:
            if si.speaker_id not in speaker_allowed:
                return Failure(
                    MangaError(
                        kind=ErrorKind.VALIDATION_FAILED,
                        message=(
                            f"Panel {panel.panel_no} Speech references "
                            f"unknown speaker_id={si.speaker_id!r}"
                        ),
                        detail={"panel_no": panel.panel_no, "speaker_id": si.speaker_id},
                    )
                )
            if si.bubble_type not in BUBBLE_TYPE_VALUES:
                return Failure(
                    MangaError(
                        kind=ErrorKind.VALIDATION_FAILED,
                        message=(
                            f"Panel {panel.panel_no} Speech has invalid bubble_type="
                            f"{si.bubble_type!r} (expected one of "
                            f"{sorted(BUBBLE_TYPE_VALUES)})"
                        ),
                        detail={"panel_no": panel.panel_no, "bubble_type": si.bubble_type},
                    )
                )

    return Success(None)


def validate_page_beat(
    parsed: ParsedPageBeat,
    *,
    known_character_ids: Sequence[str],
    known_location_ids: Sequence[str],
    expected_page_number: int | None = None,
    max_panels_per_page: int,
    known_arc_phases: Sequence[str] | None = None,
    expected_phase: str | None = None,
    expected_location_id: str | None = None,
    expected_character_ids: Sequence[str] | None = None,
) -> Result[None, MangaError]:
    """Phase 2 strict check. Returns Success(None) iff render-ready.

    `known_*` parameters set the global validity sets (existence checks).
    `expected_*` parameters add cross-layer checks against the PagePlan
    outline for this page: the LLM might emit a globally-known location_id
    or phase that doesn't match the outline, which silently drifts the
    rendered page away from the plan. When the layer code passes the
    outline values here, drift is caught at validation time.
    """
    char_set = set(known_character_ids)
    loc_set = set(known_location_ids)
    arc_set = set(known_arc_phases) if known_arc_phases is not None else None

    fm_check = _validate_frontmatter(
        parsed.frontmatter,
        expected_page_number=expected_page_number,
        known_character_ids=char_set,
        known_location_ids=loc_set,
        known_arc_phases=arc_set,
    )
    if isinstance(fm_check, Failure):
        return fm_check

    fm = parsed.frontmatter
    # cross-layer checks (after structural fm validation):
    if expected_phase is not None and fm.phase != expected_phase:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=(
                    f"PageBeat phase={fm.phase!r} does not match PagePlan outline "
                    f"phase={expected_phase!r}"
                ),
            )
        )
    if expected_location_id is not None and fm.location_id != expected_location_id:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=(
                    f"PageBeat location_id={fm.location_id!r} does not match "
                    f"PagePlan outline location_id={expected_location_id!r}"
                ),
            )
        )
    if expected_character_ids is not None:
        expected_set = set(expected_character_ids)
        # `frontmatter.character_ids` validated above to be a non-empty list.
        assert fm.character_ids is not None
        actual_set = set(fm.character_ids)
        if not actual_set.issubset(expected_set):
            extras = sorted(actual_set - expected_set)
            return Failure(
                MangaError(
                    kind=ErrorKind.VALIDATION_FAILED,
                    message=(
                        f"PageBeat character_ids={sorted(actual_set)} contain "
                        f"characters not in PagePlan outline "
                        f"character_ids={sorted(expected_set)}: extras={extras}"
                    ),
                )
            )

    assert fm.character_ids is not None
    return _validate_panels(
        parsed.panels,
        page_character_ids=set(fm.character_ids),
        max_panels_per_page=max_panels_per_page,
    )


__all__ = [
    "BUBBLE_TYPE_VALUES",
    "SIZE_HINT_VALUES",
    "SPEAKER_RESERVED_IDS",
    "ParsedFrontmatter",
    "ParsedPageBeat",
    "ParsedPanel",
    "parse_page_beat_text",
    "validate_page_beat",
]
