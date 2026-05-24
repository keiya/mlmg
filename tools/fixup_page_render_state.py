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

from mangaka.persistence import latest_state_path, load_state, save_state, state_path_for
from mangaka.result import Failure


def main() -> int:
    run_dir = Path(sys.argv[1])
    latest = latest_state_path(run_dir).unwrap()
    state = load_state(latest).unwrap()
    if state.pages is None:
        print("no pages in state", file=sys.stderr)
        return 1

    updated = []
    for p in state.pages:
        if p.beat is None:
            updated.append(p)
            continue
        png = run_dir / "pages" / f"page_{p.beat.page_number:03d}.png"
        if not png.exists():
            print(f"missing: {png}", file=sys.stderr)
            updated.append(p)
            continue
        updated.append(replace(p, image_path=png))
        print(f"i page {p.beat.page_number}: image_path = {png}")

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
