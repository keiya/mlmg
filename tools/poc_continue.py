"""PoC-only helper: continue an existing run, skipping layers already in state.

This bypasses `mangaka run`'s "fresh-run-only" guard AND `run_pipeline`'s
"always re-run from PLOT" behavior, so a checkpointed PoC can pick up where
it left off without paying for re-runs.

A proper resume mechanism belongs in `mangaka run` and is tracked under
M5 (docs/PLAN.md). Delete this script once that lands.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from dotenv import load_dotenv

from mangaka.config import RetryConfig, load_config
from mangaka.domain import MangaState
from mangaka.errors import MangaError
from mangaka.image.client_openai import OpenAIImageClient
from mangaka.layers.character import generate_character_layer
from mangaka.layers.location import generate_location_layer
from mangaka.layers.page_plan import generate_page_plan
from mangaka.layers.page_render import generate_page_render_layer
from mangaka.layers.stylist import generate_stylist_layer
from mangaka.llm.client_openai import OpenAILLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger, setup_logging
from mangaka.persistence import latest_state_path, load_state, save_state, state_path_for
from mangaka.result import Failure

logger = get_logger("poc_continue")


_REMAINING_LAYERS = [
    ("stylist", "stylist", generate_stylist_layer),
    ("character", "character", generate_character_layer),
    ("location", "location", generate_location_layer),
    ("page_plan", "page_plan", generate_page_plan),
    ("page_render", "page_render", generate_page_render_layer),
]


def _state_has(state: MangaState, layer_key: str) -> bool:
    match layer_key:
        case "stylist":
            return state.stylist is not None
        case "character":
            return len(state.characters) > 0
        case "location":
            return len(state.locations) > 0
        case "page_plan":
            return state.page_plan is not None
        case "page_render":
            return (
                state.page_plan is not None
                and len(state.pages) >= state.page_plan.total_pages
                and all(p.image_path is not None for p in state.pages)
            )
        case _:
            return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="runs/{name}/")
    parser.add_argument(
        "--until", required=True, help="Stop after this layer (stylist..page_render)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to config.toml. Default: run_dir/config.toml snapshot, "
            "fallback ./config.toml. Mirrors `mangaka export` resolution so "
            "resumes use the same limits the run was created with."
        ),
    )
    args = parser.parse_args()

    setup_logging(verbose=False, quiet=False)
    load_dotenv()

    valid_targets = {name for name, _, _ in _REMAINING_LAYERS}
    if args.until not in valid_targets:
        print(f"✗ --until must be one of {sorted(valid_targets)}", file=sys.stderr)
        return 2

    # Resolve config: explicit → snapshot → ./config.toml (mirrors `mangaka export`).
    snapshot = args.run_dir / "config.toml"
    if args.config is not None:
        config_path = args.config
    elif snapshot.exists():
        config_path = snapshot
    else:
        config_path = Path("config.toml")

    config_result = load_config(config_path)
    if isinstance(config_result, Failure):
        print(f"✗ {config_result.failure().message}", file=sys.stderr)
        return 1
    config = config_result.unwrap()

    latest_result = latest_state_path(args.run_dir)
    if isinstance(latest_result, Failure):
        print(f"✗ {latest_result.failure().message}", file=sys.stderr)
        return 1
    latest = latest_result.unwrap()
    print(f"i resuming from {latest}")

    state_result = load_state(latest)
    if isinstance(state_result, Failure):
        print(f"✗ {state_result.failure().message}", file=sys.stderr)
        return 1
    state = state_result.unwrap()

    retry_cfg = RetryConfig(**config.retry.model_dump())
    llm_retry_cfg = retry_cfg.model_copy(update={"max_retries": config.limits.max_retries})
    image_retry_cfg = retry_cfg.model_copy(
        update={"max_retries": config.limits.max_image_retries}
    )
    llm = OpenAILLMClient(default_model=config.models.default, retry_config=llm_retry_cfg)
    img = OpenAIImageClient(retry_config=image_retry_cfg)
    loader = PromptLoader()

    until_idx = next(i for i, (name, _, _) in enumerate(_REMAINING_LAYERS) if name == args.until)
    for name, state_key, fn in _REMAINING_LAYERS[: until_idx + 1]:
        if _state_has(state, name):
            print(f"i skipping {name} (already in state)")
            continue
        print(f"i running {name}")
        # Dispatch through two signatures (text-only vs image-bearing); types
        # converge to Result[MangaState, MangaError]. pyright can't infer that
        # from the call-site branch.
        if name in ("stylist", "character", "location", "page_render"):
            step_result: object = fn(state, llm, img, config, loader, run_dir=args.run_dir)  # type: ignore[call-arg]
        else:
            step_result = fn(state, llm, config, loader)  # type: ignore[call-arg]
        if isinstance(step_result, Failure):
            err = cast("MangaError", step_result.failure())  # type: ignore[attr-defined]
            print(f"✗ {name} failed: {err.message}", file=sys.stderr)
            return 1
        new_state = cast("MangaState", step_result.unwrap())  # type: ignore[attr-defined]
        state = new_state
        save_path = state_path_for(args.run_dir, state_key)
        save_result = save_state(state, save_path)
        if isinstance(save_result, Failure):
            print(f"✗ save failed: {save_result.failure().message}", file=sys.stderr)
            return 1
        print(f"  → saved {save_path.name}")

    print(f"✓ continued through {args.until}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
