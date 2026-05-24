"""Ref-image priority allocation for PageRender.

Canonical order is `style → loc → prev → chars(主要度順)`. The first three
slots are reserved; characters consume the remainder of `max_refs`. Per
SCHEMA §9, ref budget exhaustion drops characters from the tail end of
`character_ids`, preserving structural hints over secondary cast.

Returns `list[LabeledRef]` so `build_page_prompt` and `ImageClient.edit`
share the same ordered ground truth — the "N 枚目" label in the prompt
always matches `refs[N-1]`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mangaka.domain import MangaState, PageOutline


@dataclass(frozen=True)
class LabeledRef:
    """A ref image plus its human-readable role in the prompt."""

    path: Path
    label: str


def build_refs(
    state: MangaState,
    page_outline: PageOutline,
    *,
    max_refs: int,
    include_prev: bool,
) -> list[LabeledRef]:
    """Build the ordered ref list for a single PageRender call.

    Reserves slots for style + loc (+ optional prev), then fills the rest
    with character sheets in `character_ids` order. Tail characters are
    dropped if they exceed the budget.

    Caller responsibilities (NOT re-checked here):
      - `state.stylist` is not None
      - `page_outline.location_id` exists in `state.locations_by_id`
      - every `page_outline.character_ids` exists in `state.characters_by_id`
      - `max_refs >= 2` (enforced by `ImageBudgetConfig` validator)
    """
    assert state.stylist is not None
    refs: list[LabeledRef] = []

    # 1. style_ref — always present.
    refs.append(
        LabeledRef(path=state.stylist.style_ref_path, label="スタイル参照画")
    )

    # 2. location sheet.
    loc = state.locations_by_id[page_outline.location_id]
    refs.append(
        LabeledRef(
            path=loc.sheet_path,
            label=f"場所「{loc.name}」の設定画",
        )
    )

    # 3. previous page (default OFF after PoC 2026-05-24 — see config.py).
    #    style + loc are the absolute floor; prev yields when space is tight.
    prev_page = state.pages_by_number.get(page_outline.page_number - 1)
    if (
        include_prev
        and prev_page is not None
        and prev_page.image_path is not None
        and len(refs) < max_refs
    ):
        refs.append(
            LabeledRef(
                path=prev_page.image_path,
                label="直前ページの画像（連続性のため、コマ割りは真似しない）",
            )
        )

    # 4. character sheets, character_ids order (= priority order), truncated.
    char_budget = max(0, max_refs - len(refs))
    char_refs: list[LabeledRef] = []
    for char_id in page_outline.character_ids:
        char = state.characters_by_id[char_id]
        for sheet_path in char.sheet_paths:
            char_refs.append(
                LabeledRef(
                    path=sheet_path,
                    label=f"登場キャラ「{char.name}」の設定画",
                )
            )
    refs.extend(char_refs[:char_budget])

    assert len(refs) <= max_refs
    return refs


__all__ = ["LabeledRef", "build_refs"]
