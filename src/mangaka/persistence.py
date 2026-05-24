"""State persistence for `MangaState`.

state JSON stores **path references and parsed text summaries only** — no
base64-encoded image bytes, no embedded markdown source files. Canonical
artifacts live at fixed paths under `runs/{name}/` (assets/, page_beats/,
pages/) and the state JSON points at them.

M1 only persists text-only layers (Plot / Backstory / MPBV). Image layers
(Stylist / Character / Location / PagePlan / PageBeat / Page) land in M2-M4
and extend `_serialize` / `_deserialize` accordingly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from mangaka.domain import (
    MPBV,
    SFX,
    ArcPhase,
    Backstories,
    Character,
    Location,
    MangaState,
    MasterPlot,
    Page,
    PageBeat,
    PageOutline,
    PagePlan,
    Panel,
    SpeechIntent,
    Stylist,
)
from mangaka.errors import ErrorKind, MangaError
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


# Layer index → state-file basename. Centralized so naming stays consistent
# across save_state / load_state / latest_state_path.
LAYER_STATE_FILES: dict[str, str] = {
    "init": "state_00_init.json",
    "plot": "state_01_plot.json",
    "backstory": "state_02_backstory.json",
    "mpbv": "state_03_mpbv.json",
    "stylist": "state_04_stylist.json",
    "character": "state_05_character.json",
    "location": "state_06_location.json",
    "page_plan": "state_07_page_plan.json",
    # PageBeat / PageRender persist the *aggregate* state (every page's path
    # in `state.pages`); the per-page `state_08_page_beat_NN.json` granularity
    # in ARCH is M5 territory and arrives with the inject CLI.
    "page_beat": "state_08_page_beat.json",
    "page_render": "state_09_page_render.json",
    "final": "state_final.json",
}


def _serialize(state: MangaState) -> dict[str, Any]:
    """Convert `MangaState` to a JSON-safe dict."""
    data: dict[str, Any] = {
        "seed_input": state.seed_input,
        "run_name": state.run_name,
    }

    if state.master_plot is not None:
        data["master_plot"] = {"raw_markdown": state.master_plot.raw_markdown}

    if state.backstories is not None:
        data["backstories"] = {"raw_markdown": state.backstories.raw_markdown}

    if state.mpbv is not None:
        data["mpbv"] = {"raw_markdown": state.mpbv.raw_markdown}

    if state.stylist is not None:
        data["stylist"] = {
            "raw_markdown": state.stylist.raw_markdown,
            "style_ref_path": str(state.stylist.style_ref_path),
        }

    if state.characters:
        data["characters"] = [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "sheet_paths": [str(p) for p in c.sheet_paths],
            }
            for c in state.characters
        ]

    if state.locations:
        data["locations"] = [
            {
                "id": loc.id,
                "name": loc.name,
                "description": loc.description,
                "sheet_path": str(loc.sheet_path),
            }
            for loc in state.locations
        ]

    if state.pages:
        data["pages"] = [
            {
                "page_number": p.page_number,
                "image_path": (str(p.image_path) if p.image_path is not None else None),
                "beat": {
                    "page_number": p.beat.page_number,
                    "phase": p.beat.phase,
                    "location_id": p.beat.location_id,
                    "character_ids": list(p.beat.character_ids),
                    "mood": p.beat.mood,
                    "continuity_note": p.beat.continuity_note,
                    "md_path": str(p.beat.md_path),
                    "panels": [
                        {
                            "panel_no": panel.panel_no,
                            "size_hint": panel.size_hint,
                            "visual": panel.visual,
                            "emotion": panel.emotion,
                            "camera": panel.camera,
                            "speech_intents": [
                                {
                                    "speaker_id": si.speaker_id,
                                    "bubble_type": si.bubble_type,
                                    "text": si.text,
                                    "register": si.register,
                                }
                                for si in panel.speech_intents
                            ],
                            "sfx": [
                                {"text": s.text, "role": s.role} for s in panel.sfx
                            ],
                        }
                        for panel in p.beat.panels
                    ],
                },
            }
            for p in state.pages
        ]

    if state.page_plan is not None:
        pp = state.page_plan
        data["page_plan"] = {
            "total_pages": pp.total_pages,
            "arc": [
                {
                    "phase": a.phase,
                    "start_page": a.start_page,
                    "end_page": a.end_page,
                    "summary": a.summary,
                }
                for a in pp.arc
            ],
            "page_outline": [
                {
                    "page_number": po.page_number,
                    "phase": po.phase,
                    "summary": po.summary,
                    "character_ids": list(po.character_ids),
                    "location_id": po.location_id,
                }
                for po in pp.page_outline
            ],
        }

    # Page (per-page) gets a serializer in M4.
    return data


def _deserialize(data: dict[str, Any]) -> MangaState:
    """Build a `MangaState` from its JSON dict."""
    state = MangaState(
        seed_input=cast("str", data["seed_input"]),
        run_name=cast("str", data.get("run_name", "")),
    )

    if "master_plot" in data:
        mp_data = cast("dict[str, str]", data["master_plot"])
        state = replace(state, master_plot=MasterPlot(raw_markdown=mp_data["raw_markdown"]))

    if "backstories" in data:
        bs_data = cast("dict[str, str]", data["backstories"])
        state = replace(state, backstories=Backstories(raw_markdown=bs_data["raw_markdown"]))

    if "mpbv" in data:
        mpbv_data = cast("dict[str, str]", data["mpbv"])
        state = replace(state, mpbv=MPBV(raw_markdown=mpbv_data["raw_markdown"]))

    if "stylist" in data:
        s_data = cast("dict[str, str]", data["stylist"])
        state = replace(
            state,
            stylist=Stylist(
                raw_markdown=s_data["raw_markdown"],
                style_ref_path=Path(s_data["style_ref_path"]),
            ),
        )

    if "characters" in data:
        chars_data = cast("list[dict[str, Any]]", data["characters"])
        characters = [
            Character(
                id=cast("str", c["id"]),
                name=cast("str", c["name"]),
                description=cast("str", c["description"]),
                sheet_paths=[Path(p) for p in cast("list[str]", c["sheet_paths"])],
            )
            for c in chars_data
        ]
        state = replace(state, characters=characters)

    if "locations" in data:
        locs_data = cast("list[dict[str, Any]]", data["locations"])
        locations = [
            Location(
                id=cast("str", loc["id"]),
                name=cast("str", loc["name"]),
                description=cast("str", loc["description"]),
                sheet_path=Path(cast("str", loc["sheet_path"])),
            )
            for loc in locs_data
        ]
        state = replace(state, locations=locations)

    if "pages" in data:
        pages_data = cast("list[dict[str, Any]]", data["pages"])
        pages: list[Page] = []
        for p_data in pages_data:
            beat_data = cast("dict[str, Any]", p_data["beat"])
            panels_data = cast("list[dict[str, Any]]", beat_data["panels"])
            panels: list[Panel] = []
            for panel_d in panels_data:
                si_data = cast(
                    "list[dict[str, Any]]", panel_d["speech_intents"]
                )
                sfx_data = cast("list[dict[str, Any]]", panel_d["sfx"])
                panels.append(
                    Panel(
                        panel_no=cast("int", panel_d["panel_no"]),
                        size_hint=cast("str", panel_d["size_hint"]),
                        visual=cast("str", panel_d["visual"]),
                        emotion=cast("str", panel_d["emotion"]),
                        camera=cast("str | None", panel_d["camera"]),
                        speech_intents=[
                            SpeechIntent(
                                speaker_id=cast("str", si["speaker_id"]),
                                bubble_type=cast("str", si["bubble_type"]),
                                # Pre-53c4be4 state JSONs persisted this field as
                                # `intent` (meaning description) before the schema
                                # was redefined as verbatim short dialogue. Accept
                                # either key so existing runs stay loadable.
                                text=cast("str", si.get("text") or si["intent"]),
                                register=cast("str | None", si.get("register")),
                            )
                            for si in si_data
                        ],
                        sfx=[
                            SFX(
                                text=cast("str", s["text"]),
                                role=cast("str", s["role"]),
                            )
                            for s in sfx_data
                        ],
                    )
                )
            page_beat_obj = PageBeat(
                page_number=cast("int", beat_data["page_number"]),
                phase=cast("str", beat_data["phase"]),
                location_id=cast("str", beat_data["location_id"]),
                character_ids=list(cast("list[str]", beat_data["character_ids"])),
                mood=cast("str", beat_data["mood"]),
                continuity_note=cast("str | None", beat_data.get("continuity_note")),
                panels=panels,
                md_path=Path(cast("str", beat_data["md_path"])),
            )
            image_path_raw = p_data.get("image_path")
            pages.append(
                Page(
                    page_number=cast("int", p_data["page_number"]),
                    beat=page_beat_obj,
                    image_path=Path(cast("str", image_path_raw))
                    if image_path_raw is not None
                    else None,
                )
            )
        state = replace(state, pages=pages)

    if "page_plan" in data:
        pp_data = cast("dict[str, Any]", data["page_plan"])
        arc_data = cast("list[dict[str, Any]]", pp_data["arc"])
        outline_data = cast("list[dict[str, Any]]", pp_data["page_outline"])
        page_plan = PagePlan(
            total_pages=cast("int", pp_data["total_pages"]),
            arc=[
                ArcPhase(
                    phase=cast("str", a["phase"]),
                    start_page=cast("int", a["start_page"]),
                    end_page=cast("int", a["end_page"]),
                    summary=cast("str", a["summary"]),
                )
                for a in arc_data
            ],
            page_outline=[
                PageOutline(
                    page_number=cast("int", po["page_number"]),
                    phase=cast("str", po["phase"]),
                    summary=cast("str", po["summary"]),
                    character_ids=list(cast("list[str]", po["character_ids"])),
                    location_id=cast("str", po["location_id"]),
                )
                for po in outline_data
            ],
        )
        state = replace(state, page_plan=page_plan)

    return state


def to_json(state: MangaState, *, indent: int = 2) -> str:
    """Serialize `MangaState` to a JSON string (UTF-8, no ensure_ascii)."""
    return json.dumps(_serialize(state), ensure_ascii=False, indent=indent)


def from_json(json_str: str) -> Result[MangaState, MangaError]:
    """Deserialize from a JSON string."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.JSON_INVALID,
                message=f"invalid JSON: {exc}",
            )
        )

    try:
        state = _deserialize(data)
    except KeyError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message=f"missing required field: {exc}",
            )
        )
    except (TypeError, ValueError) as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message=f"failed to deserialize state: {exc}",
            )
        )
    return Success(state)


def save_state(state: MangaState, path: Path) -> Result[None, MangaError]:
    """Write `state` to `path` (parent dirs are created)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_json(state), encoding="utf-8")
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to save state: {exc}",
                detail={"path": str(path)},
            )
        )
    logger.info("state_saved", path=str(path))
    return Success(None)


def load_state(path: Path) -> Result[MangaState, MangaError]:
    """Read a state JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"state file not found: {path}",
                detail={"path": str(path)},
            )
        )
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to read state: {exc}",
                detail={"path": str(path)},
            )
        )
    return from_json(text)


def state_path_for(run_dir: Path, layer: str) -> Path:
    """Return the canonical state-file path for `layer` under `run_dir`.

    Raises `KeyError` (programmer bug) if `layer` is not a known stage name.
    """
    return run_dir / LAYER_STATE_FILES[layer]


def latest_state_path(run_dir: Path) -> Result[Path, MangaError]:
    """Find the most-recent state JSON in `run_dir`.

    Searches by ordered prefix (`state_NN_...json`) and returns the highest
    index excluding `state_final.json`. The "final" snapshot is a derived
    artifact (ARCH §state_final.json is derived) and should be regenerated,
    not used as a resume point.
    """
    if not run_dir.is_dir():
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"run directory not found: {run_dir}",
                detail={"path": str(run_dir)},
            )
        )

    candidates: list[tuple[int, Path]] = []
    for p in run_dir.glob("state_*.json"):
        name = p.name
        if name == "state_final.json":
            continue
        # state_NN_xxx.json — extract NN
        parts = name.split("_", 2)
        if len(parts) < 3:
            continue
        try:
            idx = int(parts[1])
        except ValueError:
            continue
        candidates.append((idx, p))

    if not candidates:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message=f"no state files found in {run_dir}",
                detail={"path": str(run_dir)},
            )
        )

    candidates.sort(key=lambda t: t[0])
    return Success(candidates[-1][1])


__all__ = [
    "LAYER_STATE_FILES",
    "from_json",
    "latest_state_path",
    "load_state",
    "save_state",
    "state_path_for",
    "to_json",
]
