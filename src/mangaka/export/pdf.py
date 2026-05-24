"""PDF export — A5 portrait, contain-fit, RTL viewer preferences.

reportlab + Pillow. Each page image is centered on an A5 (148×210mm) page,
scaled to fit while preserving aspect ratio (no center-crop). The PDF catalog
gets `/ViewerPreferences << /Direction /R2L >>` so right-binding readers know
to page right-to-left.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4, A5, B5  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas as rl_canvas  # type: ignore[import-untyped]

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


_PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A5": A5,
    "B5": B5,
    "A4": A4,
}


def _fit_in_box(
    image_w: int, image_h: int, box_w: float, box_h: float
) -> tuple[float, float, float, float]:
    """Return `(draw_w, draw_h, offset_x, offset_y)` centered, aspect-preserving."""
    scale = min(box_w / image_w, box_h / image_h)
    draw_w = image_w * scale
    draw_h = image_h * scale
    offset_x = (box_w - draw_w) / 2.0
    offset_y = (box_h - draw_h) / 2.0
    return draw_w, draw_h, offset_x, offset_y


def export_pdf(
    state: MangaState,
    output_path: Path,
    config: MangakaConfig,
) -> Result[Path, MangaError]:
    """Render `state.pages` into a single PDF, in page-number order.

    Pages missing `image_path` cause a `Failure(MISSING_PREREQUISITE)` —
    export expects PageRender to have completed first.
    """
    if not state.pages:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="export_pdf requires at least one rendered page",
            )
        )

    missing = [p.page_number for p in state.pages if p.image_path is None]
    if missing:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message=(
                    f"pages without image_path: {missing} — "
                    "run --until page_render first"
                ),
                detail={"missing_pages": missing},
            )
        )

    pages_sorted = sorted(state.pages, key=lambda p: p.page_number)

    page_w, page_h = _PAGE_SIZES[config.pdf.page_size]

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(output_path), pagesize=(page_w, page_h))

        # RTL viewer preference (PDF 1.7 / ISO 32000) — Adobe Reader / Preview
        # honor it for right-binding manga.
        if config.pdf.binding == "rtl":
            c.setViewerPreference("Direction", "/R2L")  # type: ignore[no-untyped-call]

        for page in pages_sorted:
            assert page.image_path is not None  # checked above
            try:
                with Image.open(page.image_path) as im:
                    image_w, image_h = im.size
            except OSError as exc:
                return Failure(
                    MangaError(
                        kind=ErrorKind.IO_ERROR,
                        message=f"failed to open page image: {exc}",
                        detail={"path": str(page.image_path)},
                    )
                )

            draw_w, draw_h, ox, oy = _fit_in_box(image_w, image_h, page_w, page_h)
            c.drawImage(  # type: ignore[no-untyped-call]
                str(page.image_path),
                ox,
                oy,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
                anchor="c",
            )
            c.showPage()

        c.save()
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"PDF write failed: {exc}",
                detail={"path": str(output_path)},
            )
        )

    logger.info(
        "pdf_exported",
        path=str(output_path),
        pages=len(pages_sorted),
        page_size=config.pdf.page_size,
        binding=config.pdf.binding,
    )
    return Success(output_path)


__all__ = ["export_pdf"]
