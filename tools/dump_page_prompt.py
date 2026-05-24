"""Dump the actual gpt-image-2 prompt for each page of a finished run."""

from __future__ import annotations

import sys
from pathlib import Path

from mangaka.config import load_config
from mangaka.image.prompts import build_page_prompt
from mangaka.image.ref_builder import build_refs
from mangaka.persistence import latest_state_path, load_state
from mangaka.result import Failure


def main() -> int:
    run_dir = Path(sys.argv[1])
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else run_dir / "config.toml"

    config = load_config(config_path).unwrap()
    latest = latest_state_path(run_dir).unwrap()
    state = load_state(latest).unwrap()

    if state.page_plan is None:
        print("state has no page_plan", file=sys.stderr)
        return 1

    for outline in sorted(state.page_plan.page_outline, key=lambda o: o.page_number):
        refs = build_refs(
            state,
            outline,
            max_refs=config.image.max_refs_per_page,
            include_prev=config.image.include_prev_page_ref,
        )
        prompt_result = build_page_prompt(state, outline, refs, config)
        if isinstance(prompt_result, Failure):
            print(f"page {outline.page_number}: BUILD FAILED: {prompt_result.failure().message}")
            continue
        prompt = prompt_result.unwrap()
        out = run_dir / f"prompt_page_{outline.page_number:03d}.txt"
        out.write_text(prompt, encoding="utf-8")
        print(f"page {outline.page_number}: {len(prompt)} chars → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
