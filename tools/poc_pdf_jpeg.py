"""PoC: build a PDF that re-encodes each page as JPEG q=92 in memory.

Same A5 portrait / contain fit / RTL binding as the real export, but uses
PIL → JPEG → ImageReader so the embedded image is JPEG instead of the
original PNG. Outputs `manga_jpeg.pdf` alongside the existing
`manga.pdf` so file sizes can be compared.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A5  # type: ignore[import-untyped]
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas as rl_canvas  # type: ignore[import-untyped]

from mangaka.persistence import latest_state_path, load_state


def _fit(image_w: int, image_h: int, box_w: float, box_h: float):
    scale = min(box_w / image_w, box_h / image_h)
    draw_w = image_w * scale
    draw_h = image_h * scale
    return draw_w, draw_h, (box_w - draw_w) / 2.0, (box_h - draw_h) / 2.0


def main() -> int:
    run_dir = Path(sys.argv[1])
    quality = int(sys.argv[2]) if len(sys.argv) > 2 else 92
    out_path = run_dir / f"manga_jpeg_q{quality}.pdf"

    state = load_state(latest_state_path(run_dir).unwrap()).unwrap()
    if state.pages is None:
        print("no pages in state", file=sys.stderr)
        return 1
    pages = sorted(
        (p for p in state.pages if p.image_path is not None),
        key=lambda p: p.beat.page_number if p.beat else 0,
    )

    page_w, page_h = A5
    c = rl_canvas.Canvas(str(out_path), pagesize=(page_w, page_h))
    c.setViewerPreference("Direction", "/R2L")  # type: ignore[no-untyped-call]

    for p in pages:
        assert p.image_path is not None
        with Image.open(p.image_path) as im:
            im_rgb = im.convert("RGB")
            buf = io.BytesIO()
            im_rgb.save(buf, "JPEG", quality=quality, optimize=True)
            buf.seek(0)
            w, h = im_rgb.size

        reader = ImageReader(buf)  # type: ignore[no-untyped-call]
        dw, dh, ox, oy = _fit(w, h, page_w, page_h)
        c.drawImage(reader, ox, oy, width=dw, height=dh)  # type: ignore[no-untyped-call]
        c.showPage()

    c.save()
    out_size = out_path.stat().st_size
    orig = run_dir / "manga.pdf"
    orig_size = orig.stat().st_size if orig.exists() else 0
    ratio = orig_size / out_size if out_size else 0
    print(f"✓ {out_path} ({out_size:,} bytes)")
    print(f"  original manga.pdf: {orig_size:,} bytes (×{ratio:.1f} larger)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
