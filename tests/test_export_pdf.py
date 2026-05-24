"""PDF export tests using PIL fixture PNGs + pikepdf for verification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pikepdf
import pytest
from _helpers import make_test_config
from PIL import Image
from returns.result import Failure, Success

from mangaka.domain import MangaState, Page
from mangaka.errors import ErrorKind
from mangaka.export.pdf import export_pdf


def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (300, 400), color=color)
    img.save(path)


def _state_with_pages(tmp_path: Path, n: int = 3) -> MangaState:
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255)]
    pages: list[Page] = []
    for i in range(1, n + 1):
        p = tmp_path / f"page_{i:03d}.png"
        _make_png(p, palette[(i - 1) % len(palette)])
        pages.append(Page(page_number=i, image_path=p))
    return MangaState(seed_input="s", run_name="r", pages=pages)


def test_export_pdf_happy_path(tmp_path: Path) -> None:
    state = _state_with_pages(tmp_path, n=3)
    config = make_test_config()
    out_path = tmp_path / "manga.pdf"
    result = export_pdf(state, out_path, config)
    assert isinstance(result, Success)
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_export_pdf_rtl_viewer_preference_set(tmp_path: Path) -> None:
    """RTL binding must emit `/ViewerPreferences << /Direction /R2L >>` in the
    PDF catalog. This is what makes Adobe Reader / Preview page right-to-left.
    """
    state = _state_with_pages(tmp_path, n=2)
    config = make_test_config()  # default pdf.binding=rtl
    out_path = tmp_path / "manga.pdf"
    assert isinstance(export_pdf(state, out_path, config), Success)

    with pikepdf.open(out_path) as pdf:
        root = pdf.Root
        vp = root.get("/ViewerPreferences")
        assert vp is not None, "PDF must declare /ViewerPreferences"
        direction = vp.get("/Direction")
        assert direction is not None
        assert str(direction) == "/R2L"


def test_export_pdf_page_count_matches(tmp_path: Path) -> None:
    state = _state_with_pages(tmp_path, n=4)
    out_path = tmp_path / "manga.pdf"
    assert isinstance(export_pdf(state, out_path, make_test_config()), Success)
    with pikepdf.open(out_path) as pdf:
        assert len(pdf.pages) == 4


def test_export_pdf_missing_image_path_fails(tmp_path: Path) -> None:
    state = _state_with_pages(tmp_path, n=2)
    # Knock out one page's image_path to simulate incomplete PageRender.
    state = replace(
        state,
        pages=[
            replace(state.pages[0], image_path=None),
            state.pages[1],
        ],
    )
    result = export_pdf(state, tmp_path / "manga.pdf", make_test_config())
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_export_pdf_no_pages_fails(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="r")
    result = export_pdf(state, tmp_path / "manga.pdf", make_test_config())
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


@pytest.mark.parametrize(("page_size", "expected_w"), [("A5", 419.527), ("A4", 595.276)])
def test_export_pdf_page_size_from_config(
    tmp_path: Path, page_size: str, expected_w: float
) -> None:
    """A5/A4 sizes from `pdf.page_size` config land in the PDF MediaBox."""
    state = _state_with_pages(tmp_path, n=1)
    config = make_test_config()
    config = config.model_copy(
        update={"pdf": config.pdf.model_copy(update={"page_size": page_size})}
    )
    out_path = tmp_path / f"out_{page_size}.pdf"
    assert isinstance(export_pdf(state, out_path, config), Success)
    with pikepdf.open(out_path) as pdf:
        mediabox = pdf.pages[0].MediaBox
        w = float(mediabox[2])
        # Tolerate sub-point rounding.
        assert abs(w - expected_w) < 1.0


def test_export_pdf_jpeg_and_png_both_succeed(tmp_path: Path) -> None:
    """Both `image_format` settings produce a valid PDF.

    A naive size comparison fails for the synthetic solid-color fixtures
    (single-color PNG with FlateDecode is smaller than JPEG headers + DCT
    overhead), but on real gpt-image-2 output JPEG is ~9× smaller. This
    test guards the structural plumbing — the size win is observed in
    real runs, not in unit fixtures.
    """
    state = _state_with_pages(tmp_path, n=3)
    base = make_test_config()
    for fmt in ("jpeg", "png"):
        cfg = base.model_copy(
            update={"pdf": base.pdf.model_copy(update={"image_format": fmt})}
        )
        out = tmp_path / f"{fmt}.pdf"
        assert isinstance(export_pdf(state, out, cfg), Success)
        assert out.exists()
        assert out.stat().st_size > 0
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) == 3


def test_export_pdf_jpeg_quality_clamped_in_config() -> None:
    """`jpeg_quality` is constrained to [1, 100] via Pydantic Field."""
    from pydantic import ValidationError

    from mangaka.config import PdfConfig

    PdfConfig(jpeg_quality=1)  # ok
    PdfConfig(jpeg_quality=100)  # ok
    with pytest.raises(ValidationError):
        PdfConfig(jpeg_quality=0)
    with pytest.raises(ValidationError):
        PdfConfig(jpeg_quality=101)
