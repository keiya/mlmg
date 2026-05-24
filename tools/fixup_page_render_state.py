"""Reconcile state with on-disk pages/page_NNN.png files.

`render_single_page.py` writes the image but doesn't update state, so
`mangaka export` (which reads `Page.image_path`) refuses. Run this after
a batch of render_single_page invocations to point the state at the
actual files on disk. Saves to state_09_page_render.json.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from mangaka.domain import Page
from mangaka.persistence import latest_state_path, load_state, save_state, state_path_for
from mangaka.result import Failure


def main() -> int:
    run_dir = Path(sys.argv[1])
    latest = latest_state_path(run_dir).unwrap()
    state = load_state(latest).unwrap()
    if state.page_plan is None:
        print("state has no page_plan — nothing to reconcile", file=sys.stderr)
        return 1

    # Build the page list from page_plan (page_render normally does this on
    # entry; we mirror that so the script works even before page_render has
    # ever been run).
    existing_by_number = {p.page_number: p for p in state.pages}
    updated: list[Page] = []
    for outline in state.page_plan.page_outline:
        png = run_dir / "pages" / f"page_{outline.page_number:03d}.png"
        if not png.exists():
            print(f"missing: {png}", file=sys.stderr)
            existing = existing_by_number.get(outline.page_number)
            updated.append(
                existing or Page(page_number=outline.page_number, image_path=None)
            )
            continue
        existing = existing_by_number.get(outline.page_number)
        if existing is not None:
            updated.append(replace(existing, image_path=png))
        else:
            updated.append(Page(page_number=outline.page_number, image_path=png))
        print(f"i page {outline.page_number}: image_path = {png}")

    new_state = replace(state, pages=updated)
    out = state_path_for(run_dir, "page_render")
    save_result = save_state(new_state, out)
    if isinstance(save_result, Failure):
        print(f"✗ {save_result.failure().message}", file=sys.stderr)
        return 1
    print(f"✓ wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
