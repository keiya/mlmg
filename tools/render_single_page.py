"""Render one specific page from an existing run with the current prompt builder.

Useful for iterating on prompt engineering: change build_page_prompt /
SECTION_SETS, then re-render a single page to compare output without
re-running the whole pipeline. Saves to `pages/page_NNN.png` (overwrites
the existing one — back up first if you want to compare versions).
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from mangaka.config import RetryConfig, load_config
from mangaka.image.client_openai import OpenAIImageClient
from mangaka.image.prompts import build_page_prompt
from mangaka.image.ref_builder import build_refs
from mangaka.persistence import latest_state_path, load_state, save_state, state_path_for
from mangaka.result import Failure


def main() -> int:
    load_dotenv()
    run_dir = Path(sys.argv[1])
    page_number = int(sys.argv[2])
    config_path = Path(sys.argv[3]) if len(sys.argv) > 3 else run_dir / "config.toml"

    config = load_config(config_path).unwrap()
    latest = latest_state_path(run_dir).unwrap()
    state = load_state(latest).unwrap()

    if state.pages is None:
        print("no pages in state", file=sys.stderr)
        return 1

    page = next(
        (p for p in state.pages if p.beat is not None and p.beat.page_number == page_number),
        None,
    )
    if page is None or page.beat is None:
        print(f"page {page_number} not found", file=sys.stderr)
        return 1

    refs = build_refs(
        state,
        page.beat,
        max_refs=config.image.max_refs_per_page,
        include_prev=config.image.include_prev_page_ref,
    )
    prompt_result = build_page_prompt(state, page.beat, refs, config)
    if isinstance(prompt_result, Failure):
        print(f"prompt build failed: {prompt_result.failure().message}", file=sys.stderr)
        return 1
    prompt = prompt_result.unwrap()
    print(f"i prompt: {len(prompt)} chars, {len(refs)} refs")

    image_retry_cfg = RetryConfig(**config.retry.model_dump()).model_copy(
        update={"max_retries": config.limits.max_image_retries}
    )
    img = OpenAIImageClient(retry_config=image_retry_cfg)

    print(f"i calling gpt-image-2 for page {page_number}...")
    edit_result = img.edit(
        prompt,
        refs=[ref.path for ref in refs],
        size=config.image_provider.default_size,
        quality=config.image_provider.quality,
        model=config.image_provider.model,
    )
    if isinstance(edit_result, Failure):
        print(f"render failed: {edit_result.failure().message}", file=sys.stderr)
        return 1
    img_bytes = edit_result.unwrap()
    out = run_dir / "pages" / f"page_{page_number:03d}.png"
    out.write_bytes(img_bytes)
    print(f"✓ wrote {out} ({len(img_bytes)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
